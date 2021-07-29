# racktables-to-netbox

Scripts to export Racktables data, accessible through a SQL connection, into a [Netbox](https://github.com/netbox-community/netbox/) instance, accessible at a URL. An easy way to test NB is with [netbox-docker](https://github.com/netbox-community/netbox-docker). Some benefits of Netbox are a strictly enforced naming and relationship hierarchy, custom scripts and reports, easy REST API with many wrappers [like this one](https://github.com/jagter/python-netbox). The `migrate.py` script will transfer:
- Racks at sites
- Device locations in racks and reservations
- All unracked stuff, notably VMs and clusters
- Parent child relationships like servers in chassises, patch panels in patch panels
- IPs, networks, VLANs
- Interfaces and their associated IP. Note that if an "OS interface" in "IP addresses" is same as "local name" in "ports and links," the interface is not duplicated
- Connections between interfaces really the 'ports and links' catagory
- Tags, labels, asset numbers

## Files:
**migrate.py**

Migrate data from RT to NB. Meant to be run once without interuption, although some bools exist to skip steps.
Steps that depend on others create cached data on disk, but the best procedure is to fully run once on an empty NB instance. For certain interfaces, names are capitalized or have string replacement. See comments for details or to turn off.

Python package requirements: `python3 -m pip install python-netbox python-slugify`

**custom_fields.yml**

The file to supply to the Netbox instance for custom fields. Thrse fields are expected by the migrate script and must be there.

**vm.py**

Update the uniquely named VMs in NB with memory, disk and cpu data from RHEVM instances. Because two VMs can be in separate clusters with the same name and there is no mapping between RT cluster names and RHEVM cluster names, any not uniquely named VM is ignored. 
Code is there to compare NICs and IPs as well.

Python package requirements `python3 -m pip install python-netbox bs4`

**free.py**

List the number of free IP addresses in NB based on the tags on prefixes.

Python package requirements `python3 -m pip install python-netbox`

## Notes on python-netbox:
- As of July 2021 the pip code is not up to date to the Github repo, so you must manually update the `dcim.py` file's method `create_interface_connection` to match the up to date one on Github.
- As of July 2021 [this PR](https://github.com/jagter/python-netbox/pull/49) hasn't been merged, so the `get_device_bays` method is not yet in `dcim.py` and must be added manually.
