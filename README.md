# racktables-to-netbox

Scripts to export Racktables data, accessible through a SQL connection, into a Netbox instance, accessible at a URL.
Some benefits of Netbox are a strictly enforced naming and relationship hierarchy, custom scripts, cutom reports, easy REST API with many wrappers (like this one)[https://github.com/jagter/python-netbox].

## Files:
**migrate.py**
Migrate data from RT to NB. Meant to be run once without interuption, although some bools exist to skip steps.
Steps that depend on others create cached data on disk, but the best procedure is to fully run once on an empty NB instance.

Package requirements: python-netbox, python-slugify

**vm.py**
Update the uniquely named VMs in NB with memory, disk and cpu data from RHEVM instances.

Package requirements: python-netbox, python-slugify, bs4

**free.py**

List the number of free IP addresses in NB based on tags on prefixes.

Package requirements: python-netbox
