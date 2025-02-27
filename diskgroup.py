#!/usr/bin/python
import atexit
import ssl
import sys
from pyVim import connect
from pyVmomi import vim
import vsanmgmtObjects
import vsanapiutils

def main():
    server = sys.argv[1] 
    try:
        si = vc_connect(server)
    except SystemExit as e:
        print(f"Failed to connect to vCenter: {e}")
        return  # Exit the main function if connection fails

    atexit.register(connect.Disconnect, si)
    content = si.RetrieveContent()

    # Get a list of ESXi hosts
    container_view = content.viewManager.CreateContainerView(content.rootFolder, [vim.HostSystem], True)
    esxi_hosts = container_view.view
    container_view.Destroy() # Important: Destroy the view to release resources

    if not esxi_hosts:
        print("No ESXi hosts found in the vCenter inventory.")
        return

    for esxihost in esxi_hosts:
        print(f"Processing host: {esxihost.name}")

        diskmap = {esxihost: {'cache': [], 'capacity': []}}
        cacheDisks = []
        capacityDisks = []
        result = esxihost.configManager.vsanSystem.QueryDisksForVsan()
        ssds = []

        for ssd in result:
            if ssd.state == 'eligible' and (ssd.disk.capacity.block) / 2 / 1024 / 1024 > 99:
                ssds.append(ssd.disk)

        if ssds:
            smallerSize = min([disk.capacity.block * disk.capacity.blockSize for disk in ssds])
            for ssd in ssds:
                size = ssd.capacity.block * ssd.capacity.blockSize
                if size == smallerSize:
                    diskmap[esxihost]['cache'].append(ssd)
                    cacheDisks.append((ssd.displayName, size, esxihost.name))
                else:
                    diskmap[esxihost]['capacity'].append(ssd)
                    capacityDisks.append((ssd.displayName, size, esxihost.name))

            tasks = []
            context = ssl.create_default_context()
            context.check_hostname = False  # Consider removing in production
            context.verify_mode = ssl.CERT_NONE  # Consider removing in production
            vcMos = vsanapiutils.GetVsanVcMos(si._stub, context=context)
            vsanVcDiskManagementSystem = vcMos['vsan-disk-management-system']

            for host, disks in diskmap.items():
                if disks['cache'] and disks['capacity']:  # Crucial check here
                    dm = vim.VimVsanHostDiskMappingCreationSpec(
                        cacheDisks=disks['cache'], capacityDisks=disks['capacity'],
                        creationType='allFlash',
                        host=host)
                    task = vsanVcDiskManagementSystem.InitializeDiskMappings(dm)
                    tasks.append(task)
                else:
                    print(f"No suitable cache and capacity disks found for host: {host.name}. Skipping VSAN configuration for this host.")

            if tasks:  # only wait for tasks if there are any
                vsanapiutils.WaitForTasks(tasks, si)
            else:
                print("No VSAN disk mapping tasks to execute.")

        else:
            print('No eligible disks found for VSAN might have already been set up on host:', esxihost.name)

def get_obj(content, vimtype, name):
    # ... (No changes needed)
    obj = None
    container = content.viewManager.CreateContainerView(
        content.rootFolder, vimtype, True)
    for c in container.view:
        if c.name == name:
            obj = c
            break
    return obj

def vc_connect(vc):
    context = ssl.create_default_context()
    context.check_hostname = False  # Consider removing in production
    context.verify_mode = ssl.CERT_NONE  # Consider removing in production
    service_instance = None

    try:
        print(f'Attempting to connect to {vc}')
        service_instance = connect.SmartConnect(host=vc,
                                                user=sys.argv[2],
                                                pwd=sys.argv[3],
                                                port=443,
                                                sslContext=context)

    except IOError as e:
        raise SystemExit(f"IOError: {e}") # Raise SystemExit with error message
    except Exception as e: # Catch other connection exceptions
        raise SystemExit(f"Connection error: {e}")

    if not service_instance:
        raise SystemExit("Unable to connect to host with supplied info.")
    return service_instance


if __name__ == '__main__':
    main()
