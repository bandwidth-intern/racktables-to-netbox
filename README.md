# racktables-to-netbox

Scripts to export Racktables data, accessible through a SQL connection, into a [Netbox](https://github.com/netbox-community/netbox/) instance, accessible at a URL. An easy way to test NB is with [netbox-docker](https://github.com/netbox-community/netbox-docker). Some benefits of Netbox are a strictly enforced naming and relationship hierarchy, custom scripts and reports, easy REST API with many wrappers [like this one](https://github.com/jagter/python-netbox). The `migrate.py` script will transfer:
- Racks at sites
- Device locations in racks and reservations
- All unracked stuff, notably VMs and their clusters
- Parent child relationships like servers in chassises, patch panels in patch panels
- IPs, networks, VLANs
- Interfaces and their associated IP. Note that if an "OS interface" in "IP addresses" is same as "local name" in "ports and links," the interface is not duplicated
- Connections between interfaces really the 'ports and links' catagory
- Tags, labels, asset numbers (still need to make sure asset nos are grabbed from everywhere)

## Files:
**migrate.py**

Migrate data from RT to NB. Meant to be run once without interuption, although some bools exist to skip steps.
Steps that depend on others create cached data on disk, but the best procedure is to fully run once on an empty NB instance.

Package requirements: python-netbox, python-slugify

**vm.py**

Update the uniquely named VMs in NB with memory, disk and cpu data from RHEVM instances.
Code is there to compare NICs and IPs as well.

Package requirements: python-netbox, bs4

**free.py**

List the number of free IP addresses in NB based on tags on prefixes.

Package requirements: python-netbox
