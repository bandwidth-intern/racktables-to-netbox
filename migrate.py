from netbox import NetBox
import pymysql
from slugify import slugify
import pickle
import os
import time
import ipaddress
import random
import threading

# Messy script to transfer Racktables SQL to NetBox
# Set "MAX_PAGE_SIZE=0" in "env/netbox.env"
# Add the printed custom_fields to initialization/custom_fields.yaml for all the fields from Racktables

# Set all the bools to True and run once through for correct result, they were for debugging problems. Some info is cached with pickle, though

CREATE_VLAN_GROUPS =           True
CREATE_VLANS =                 True
# This also creates the clusters, which are needed for all devices
CREATE_MOUNTED_VMS =           True
CREATE_UNMOUNTED_VMS =         True
CREATE_RACKED_DEVICES =        True
# Non racked devices depend on racked devices being created first
CREATE_NON_RACKED_DEVICES =    True
# Interfaces rely on devices being created
CREATE_INTERFACES =            True
# Interface connections depend on all interfaces created
CREATE_INTERFACE_CONNECTIONS = True
CREATE_IPV4 =                  True
CREATE_IPV6 =                  True
# IP space depends on interfaces being created
CREATE_IP_NETWORKS =           True
CREATE_IP_ALLOCATED =          True
CREATE_IP_NOT_ALLOCATED =      True


# The length to exceed for a site to be considered a location (like an address) not a site
SITE_NAME_LENGTH_THRESHOLD = 10

# Each step may cache some data relevant to the next step. This will stop that from happening in the pickle load function
STORE_DATA = False

rt_host = '127.0.0.1'
rt_port = 3306
rt_user = 'root'
rt_db = 'test1'
connection = pymysql.connect(host=rt_host,user=rt_user,db=rt_db, port=rt_port)

nb_host = '10.248.48.4'
nb_port = 8001
nb_token = '0123456789abcdef0123456789abcdef01234567'

netbox = NetBox(host=nb_host, port=nb_port, use_ssl=False, auth_token=nb_token)

# This might not be all. Used for looking up non-racked items. Key names are for reference
objtype_id_names = {
1: "BlackBox",
2: "PDU",
3: "Shelf",
4: "Server",
5: "DiskArray",
7: "Router",
8: "Network Switch",
9: "Patch Panel",
10: "CableOrganizer",
11: "spacer",
12: "UPS",
13: "Modem",
15: "console",
447: "multiplexer",
798: "Network Security",
1502: "Server Chassis",
1398: "Power supply",
1503: "Network chassis",
1644: "serial console server",
1787: "Management interface",
50003: "Circuit",
50013: "SAN",
50044: "SBC",
50064: "GSX",
50065: "EMS",
50066: "PSX",
50067: "SGX",
50083: "SBC SWE",
# Don't create these with the unracked devices
# 1504: "VM",
# 1505: "VM Cluster",
# 1560: "Rack",
# 1561: "Row",
# 1562: "Location",
}

# Manufacturer strings that exist in RT. Pulled out of "HW Type" to set as the manufacturer
racktables_manufacturers = {'Generic', 'Dell', 'MicroSoft', 'F5', 'ExtremeXOS', 'Netapp', 'Open Solaris', 'EMC', 'SlackWare', 'RH', 'FreeBSD', 'Edge-Core', 'SMC', 'Force10', 'Cyclades', 'IBM', 'Linksys', 'IronWare', 'Red', 'Promise', 'Extreme', 'QLogic', 'Marvell', 'SonicWall', 'Foundry', 'Juniper', 'APC', 'Raritan', 'Xen', 'NEC', 'Palo', 'OpenSUSE', 'Sun', 'noname/unknown', 'NetApp', 'VMware', 'Moxa', 'Tainet', 'SGI', 'Mellanox', 'Vyatta', 'Raisecom', 'Gentoo', 'Brocade', 'Enterasys', 'Dell/EMC', 'VMWare', 'Infortrend', 'OpenGear', 'Arista', 'Lantronix', 'Huawei', 'Avocent', 'SUSE', 'ALT_Linux', 'OpenBSD', 'Nortel', 'Univention', 'JunOS', 'MikroTik', 'NetBSD', 'Cronyx', 'Aten', 'Intel', 'PROXMOX', 'Ubuntu', 'Motorola', 'SciLin', 'Fujitsu', 'Fiberstore', '3Com', 'D-Link', 'Allied', 'Fortigate', 'Debian', 'HP', 'NETGEAR', 'Pica8', 'TPLink', 'Fortinet', 'RAD', 'NS-OS', 'Cisco', 'Alcatel-Lucent', 'CentOS', 'Hitachi'}

# Pairs of parent objtype_id, then child objtype_id
parent_child_objtype_id_pairs = (
	(1502, 4),# Server inside a Server Chassis
	(9, 9),# Patch Panel inside a Patch Panel
)

# Some interfaces might have a name including "Eth", then have an IP with name "Ethernet"
# This dict will try to eliminate the difference to clean up the number of "Virtual" and "Other" type interfaces
# Convert the short name into the long name
# These only apply to objects of type "Router", 7, and "Network switch", 8
interface_name_mappings = {
	"Eth": "Ethernet",
	"eth": "Ethernet",
	"ethernet": "Ethernet",

	"Po": "Port-Channel",
	"Port-channel": "Port-Channel",

	"BE": "Bundle-Ether",
	"Lo": "Loopback",
	"Loop": "Loopback",
	"Vl": "VLAN",
	"Vlan": "VLAN",
	"Mg": "MgmtEth",
	"Se": "Serial",
	"Gi": "GigabitEthernet",
	"Te": "TenGigE",
	"Tw": "TwentyFiveGigE",
	"Fo": "FortyGigE",
	"Hu": "HundredGigE",
}

parent_objtype_ids = [pair[0] for pair in parent_child_objtype_id_pairs]

global_names = set()
global_tags = set()
global_devices = list()
global_device_roles = list()
global_manufacturers = list()
global_device_types = list()

# When looking at all physical devices, store the SQL object_id and the to use in the Port table later
global_physical_object_ids = set()

# Get the same info for non physical devices like VMs and Servers mounted in chassises to create their ports and linterfaces
# This is filled in during create_non_racked_devices function
global_non_physical_object_ids = set()

# asset_no from racktables. Used to find the duplicates and add -1
asset_tags = set()

# object_id to "Chassis Serial" number if it exists
serials = dict()

# Used for separating identical objects in different spots in the same rack
# Have not ca;lculated overflow yet, but 32-126 is a lot for one rack of 45/2 slots for items
first_ascii_character = " "

# Turn the attr_id from table "Attribute" to a slugified string name for example 3 -> "FQDN"
slugified_attributes = dict()

# Turn the uint_value for attr_id 2 in table "AttributeValue" into a string from the table "Dictionary"
hw_types = dict()

def error_log(string):
	with open("errors", "a") as error_file:
		error_file.write(string + "\n")

def pickleLoad(filename, default):
	if os.path.exists(filename):
		file = open(filename, 'rb')
		data = pickle.load(file)
		file.close()
		return data
	return default

def pickleDump(filename, data):
	if STORE_DATA:
		file = open(filename, 'wb')
		pickle.dump(data, file)
		file.close()
		
def getRackHeight(cursor, rackId):
	cursor.execute("SELECT uint_value FROM AttributeValue WHERE object_id={} AND attr_id=27;".format(rackId))
	return cursor.fetchall()[0][0]
		
# return the "HW Type" for the given racktables object
def get_hw_type(racktables_object_id):
	global hw_types
	cursor.execute("SELECT uint_value FROM AttributeValue WHERE object_id={} AND attr_id=2;".format(racktables_object_id))
	uint = cursor.fetchall()
	return hw_types[uint[0][0]] if uint else None

def getRowsAtSite(cursor, siteId):
	rows = []
	cursor.execute("SELECT child_entity_id FROM EntityLink WHERE parent_entity_type='location' AND parent_entity_id=%s AND child_entity_type='row'",siteId)
	rowIds = cursor.fetchall()
	for rowId in rowIds:
		cursor.execute("SELECT id,name,label,asset_no,comment FROM Object WHERE id=%s",rowId[0])
		rows += cursor.fetchall()
	return rows

def getRacksAtRow(cursor, rowId):
	racks = []
	cursor.execute("SELECT child_entity_id FROM EntityLink WHERE parent_entity_type='row' AND parent_entity_id=%s AND child_entity_type='rack'",rowId)
	rackIds = cursor.fetchall()
	for rackId in rackIds:
		cursor.execute("SELECT id,name,label,asset_no,comment FROM Object WHERE id=%s", rackId[0])
		racks += cursor.fetchall()
	return racks

def getAtomsAtRack(cursor, rackId):
	cursor.execute("SELECT rack_id,unit_no,atom,state,object_id FROM RackSpace WHERE rack_id={};".format(rackId))
	return cursor.fetchall()

def getTags(cursor, entity_realm, entity_id):
	tags = []
	cursor.execute("SELECT tag_id FROM TagStorage WHERE entity_id={} AND entity_realm=\"{}\";".format(entity_id, entity_realm))
	for tag_id in [x[0] for x in cursor.fetchall()]:
		cursor.execute("SELECT tag FROM TagTree WHERE id={};".format(tag_id))
		tags += cursor.fetchall()
	return [{'name': tag[0]} for tag in tags]

# Return a string
def getDeviceType(cursor, objtype_id):
	cursor.execute("SELECT dict_key,dict_value FROM Dictionary WHERE dict_key={};".format(objtype_id))
	return cursor.fetchall()[0][1]

def get_manufacturer_role_type(cursor, racktables_object_id, objtype_id, height, is_full_depth):

	global racktables_manufacturers
	
	original_device_type = getDeviceType(cursor, objtype_id)
	manufacturer = original_device_type

	# Add the height to the type model, as well as the binary full_depth or not
	hw_type = get_hw_type(racktables_object_id)
	if hw_type:
		# print("HW:", hw_type)
		device_type = hw_type

		for racktables_manufacturer in racktables_manufacturers:
			if device_type.startswith(racktables_manufacturer) or device_type.startswith(racktables_manufacturer+" "):
				device_type = device_type.replace(racktables_manufacturer," ", 1).lstrip(" ")
				manufacturer = racktables_manufacturer
	else:
		device_type = original_device_type

	device_type_model = "{}-{}U{}".format(device_type, height, "-full" if is_full_depth else "")

	return manufacturer, original_device_type, device_type_model


def create_global_tags(tags):
	global global_tags
	for tag in tags:
		if tag not in global_tags:
			try:
				netbox.extras.create_tag(tag, slugify(tag))
			except:
				print(tag)
			global_tags.add(tag)

def createDeviceAtLocationInRack(device_name, face, start_height, device_role, manufacturer, device_type_model, site_name, rack_name, asset_no, racktables_device_id):
	global global_devices
	global global_names
	global global_device_roles
	global global_manufacturers
	global global_device_types
	global asset_tags

	name_at_location = None
	id_at_location = None

	for device in global_devices:
		if face == device['face']['value'] and start_height == device['position'] and device_role == device['device_role']['name'] and manufacturer == device['device_type']['manufacturer']['name'] and device_type_model == device['device_type']['model'] and site_name == device['site']['name'] and rack_name == device['rack']['name']:
			name_at_location = device['name']
			id_at_location = device['id']
			break

	if name_at_location == None:
		# print(device_name, "being created at", rack_name, start_height, face)
		name_at_location = device_name

		if device_name in global_names:

			name_counter = 1
			while True:
				counter_name = device_name + ".{}".format(name_counter)
				if counter_name not in global_names:
					
					name_at_location = counter_name
					break

				else:
					name_counter += 1

		# Check if the device is in a VM cluster and if so add it to that when creating it in Netbox
		device_in_vm_cluster, device_vm_cluster_name, parent_entity_ids = device_is_in_cluster(racktables_device_id)
		custom_fields = get_custom_fields(cursor, racktables_device_id)
		serial = serials[racktables_device_id] if racktables_device_id in serials else ""

		asset_no = asset_no.strip() if asset_no else None
		if asset_no and asset_no in asset_tags:
			asset_no = asset_no+ "-1"

		device = netbox.dcim.create_device(custom_fields=custom_fields,face=face,cluster={"name":device_vm_cluster_name} if device_in_vm_cluster else None,asset_tag=asset_no,serial=serial,position=start_height,name=name_at_location,device_role=device_role,manufacturer={"name":manufacturer},device_type=device_type_model,site_name=site_name,rack={"name":rack_name})
		asset_tags.add(asset_no)

		id_at_location = device['id']

		global_names.add(name_at_location)
		global_devices.append(device)

	else:
		print(name_at_location, "exists at location")

	return name_at_location, id_at_location

# Pass the list of atoms into this and have the devices built to the appropriate size
def createObjectsInRackFromAtoms(cursor, atoms, rack_name, rack_id):
	
	debug_splits = False
	
	global global_physical_object_ids

	# Put positions into dict based on Id
	atoms_dict = {}
	for atom in atoms:
		key = str(atom[4])
		if key not in atoms_dict:
			atoms_dict[key] = [atom]
		else:
			atoms_dict[key].append(atom)

	# Some of the same devices might exist, but not be attached:
	# For example: [(1373, 18, 'rear', 'T', 1071), (1373, 19, 'rear', 'T', 1071), (1373, 35, 'front', 'T', 1071), (1373, 36, 'front', 'T', 1071)]
	# Should be two separate items because they do not touch
	# Iterate over the list and separate it at points where the objects do not meet.
	# Because the original was dict, add a dummy value to the end of the Id key and disregard that for gettign the real id
	
	added_atom_objects = {}
	separated_Ids = False

	for Id in atoms_dict:
		current_counter = 0
		old_counter = 0
		max_counter = len(atoms_dict[Id]) - 1
		current_hash_addition = first_ascii_character # The value to add onto the Id. Make sure this stays as 1 character and increment as ASCII
		current_atom = atoms_dict[Id][0][2]
		current_height = atoms_dict[Id][0][1]
		# When separating the Ids, make sure to remove the original Id from the atoms_dict
		internal_separated_Ids = False
		
		# There could be a single item at the end of a list like:
		# [(1379, 5, 'front', 'T', 1070), (1379, 6, 'front', 'T', 1070), (1379, 9, 'front', 'T', 1070), (1379, 10, 'front', 'T', 1070), (1379, 35, 'front', 'T', 1070)]
		# Where the final list adds things before, but not itself, so add everything after the last_added

		# Iterate over a copy of atoms_dict[Id] list of atoms so that the original lsit can have items removed to use 0 as starting place and not keep track of it
		for atom in atoms_dict[Id].copy():

			# Cases without overlap, where a split should be made
			# [1] [1] [ ]
			# [ ] [1] [1] # Disregard this case because it doesn't appear to come up and is too much to calculate horizantal or vertical
			# [ ] [ ] [ ]

			# [1] [1] [ ]    [1] [ ] [ ] 
			# [ ] [ ] [ ] or [ ] [ ] [1] # Check for separation of heights here
			# [ ] [1] [1]    [ ] [ ] [ ] 

			if debug_splits:
				print(atom[1], current_height)

			# Look for device on a height above the last device
			# Once found a split based on the last 
			if atom[1] > current_height + 1 and current_counter > 0: # or (internal_separated_Ids == True and current_counter == max_counter):
				# Create separate Id for all the atoms in this list before the current one

				if debug_splits:
					print(atoms_dict[Id], current_counter, old_counter)

				# Resize the original atoms_dict to remove the first atoms
				added_atom_objects[Id + current_hash_addition] = atoms_dict[Id][old_counter:current_counter]

				if debug_splits:
					print("after", added_atom_objects[Id + current_hash_addition])
					print(current_counter == max_counter)


				# Inc hash addition. NO CHECK FOR OVERFLOW, although 32 to 126 should be good for one rack of ids
				current_hash_addition = str(chr(ord(current_hash_addition) + 1))

				internal_separated_Ids = True
				separated_Ids = True
				old_counter = current_counter

			#Calculate the current position and determine if it touches the last position in the ordered list.
			current_atom = atom[2]
			current_height = atom[1]
			current_counter += 1

		# Add the last few items		
		if internal_separated_Ids == True:
			added_atom_objects[Id + current_hash_addition] = atoms_dict[Id][old_counter:]
			# print(added_atom_objects[Id + current_hash_addition])

	# Add all the key,value pairs from added_atom_objects to the original atoms_dict and then remove the original Ids
	if separated_Ids == True:

		# Add the new Ids atoms lists with the hash addition to the original atoms_dict
		for Id_and_addition in added_atom_objects:
			atoms_dict[Id_and_addition] = added_atom_objects[Id_and_addition]
		
		# Remove the original ids from atoms_dict since the value list should now be blank
		for Id_and_addition in added_atom_objects:
			
			original_Id = Id_and_addition[:-1]
			if original_Id in atoms_dict:
				atoms_dict.pop(original_Id)
		
		if debug_splits:
			print(added_atom_objects)
			print("separated", atoms_dict)
	
	# Any other Ids that did not get an added character now get first_ascii_character added to them
	remove_original_Ids = []
	add_new_Ids = {}
	for Id in atoms_dict:
		if Id not in added_atom_objects:
			add_new_Ids[Id + first_ascii_character] = atoms_dict[Id]
			remove_original_Ids.append(Id)

	for Id in add_new_Ids:
		atoms_dict[Id] = add_new_Ids[Id]

	# Remove the original Ids without the hash addition to the atoms_dict
	for Id in remove_original_Ids:
		atoms_dict.pop(Id)

	# Start to calculate sizes and add devices
	for Id in atoms_dict:
		
		# Cut off the extra character added to distinguish the same device in multiple locations in a rack
		
		start_height = min([atom[1] for atom in atoms_dict[Id]])
		height = max([atom[1] for atom in atoms_dict[Id]]) - start_height + 1

		# Should this be == str or startswith if there are multiple reservation splits?
		if Id == str(None) + first_ascii_character:
			try:
				units = list(range(start_height, start_height+height))

				print("Reservation")
				netbox.dcim.create_reservation(rack_num=rack_id,units=units,description=".",user='admin')

			except Exception as e:
				print(str(e))

			continue

		real_id = int(Id[:-1])

		cursor.execute("SELECT id,name,label,objtype_id,has_problems,comment,asset_no FROM Object WHERE id={};".format(real_id))
		info = cursor.fetchall()[0]
		objtype_id = info[3]
		device_name = info[1]
		asset_no = info[-1]
		
		device_tags = getTags(cursor, "object", real_id)
		
		# Whether front only, rear only, or both
		if 'rear' not in [atom[2] for atom in atoms_dict[Id]]:
			face = 'front'
			is_full_depth = False
		elif 'front' not in [atom[2] for atom in atoms_dict[Id]]:
			face = 'rear'
			is_full_depth = False
		else:
			# face = 'both'
			# There is no 'both' in netbox, so use 'front' instead
			face = 'front'
			is_full_depth = True
		
		manufacturer, device_role, device_type_model = get_manufacturer_role_type(cursor, real_id, objtype_id, height, is_full_depth)

		if device_role not in global_device_roles:
			netbox.dcim.create_device_role(device_role,"ffffff",slugify(device_role))
			global_device_roles.add(device_role)

		if manufacturer not in global_manufacturers:
			netbox.dcim.create_manufacturer(manufacturer, slugify(manufacturer))
			global_manufacturers.add(manufacturer)

		# Create a device type that takes into account the height
		# If the device is a "Server Chassis", objtype_id 1502, create it as a parent device to assign children to in device bays
		if objtype_id in parent_objtype_ids:
			device_type_model += "-parent"

		# Cannot easily check device_types, so must use a try: except: here
		if device_type_model not in global_device_types:
			netbox.dcim.create_device_type(model=device_type_model,manufacturer={"name":manufacturer},slug=slugify(device_type_model),u_height=height,is_full_depth=is_full_depth,tags=device_tags,subdevice_role="parent" if objtype_id in parent_objtype_ids else "")
			global_device_types.add(device_type_model)

		# Naming check done first, then check for existance in specific slot since lots of a racks have many devices of the same name, which is not allowed in netbox, even accross racks, sites, etc

		# Try to create a device at specific location. 
		# Function looks for the location to be open, then tries different names since device names must be unique
		device_name, device_id = createDeviceAtLocationInRack(device_name=device_name, face=face, start_height=start_height, device_role=device_role, manufacturer=manufacturer, device_type_model=device_type_model,site_name= site_name,rack_name=rack_name, asset_no=asset_no, racktables_device_id=real_id)

		# Store all the device object_ids and names in the rack to later create the interfaces and ports
		global_physical_object_ids.add((device_name, info[0], device_id, objtype_id))

# Necessary to split get_interfaces() calls because the current 50,000 interfaces fails to ever return
def get_interfaces():

	interfaces = []
	interfaces_file = "interfaces"
	
	limit = 500
	offset = 0

	# Uncomment this if created interfaces successfully previously and have their data in the file
	# or get_interfaces_custom was not added (likely) and you are only running the script once without error
	return pickleLoad(interfaces_file, [])

	while True:
		# In netbox-python dcim.py I defined this as: Some issue with setting limit and offset made it necessary
		# def get_interfaces_custom(self, limit, offset, **kwargs):
 		#       return self.netbox_con.get('/dcim/interfaces', limit=limit, offset=offset, **kwargs)
		ret = netbox.dcim.get_interfaces_custom(limit=limit, offset=offset)
		if ret:
			interfaces.extend(ret)
			offset += limit
			print("Added {} interfaces, total {}".format(limit, len(interfaces)))
		else:
			pickleDump(interfaces_file, interfaces)
			return interfaces

def device_is_in_cluster(device_id):
	cursor.execute("SELECT parent_entity_id FROM EntityLink WHERE parent_entity_type=\"object\" AND child_entity_id={};".format(device_id))
	parent_entity_ids = [parent_entity_id[0] for parent_entity_id in cursor.fetchall()]
	
	for parent_entity_id in parent_entity_ids:
		cursor.execute("SELECT objtype_id,name FROM Object WHERE id={};".format(parent_entity_id))
		parent_objtype_id,parent_name = cursor.fetchall()[0]

		if parent_objtype_id == 1505:
			return True, parent_name, parent_entity_ids

	return False, None, parent_entity_ids

def get_custom_fields(cursor, racktables_object_id, initial_dict=None):
	
	global slugified_attributes
	custom_fields = initial_dict if initial_dict else dict()

	cursor.execute("SELECT attr_id,string_value,uint_value FROM AttributeValue WHERE object_id={};".format(racktables_object_id))
	attributes = cursor.fetchall()

	for attr_id,string_value,uint_value in attributes:
		
		# Skip the HW Type because this is used for the type and height and "Serial Tag"
		if attr_id == 2 or attr_id == 27 or attr_id == 10014:
			continue

		custom_fields[slugified_attributes[attr_id]] = string_value if string_value else uint_value

	return custom_fields

# Create the device in this list and return those that could not be created because the parent did not exist yet
def create_parent_child_devices(cursor, data, objtype_id):

	global global_non_physical_object_ids

	existing_site_names = set(site['name'] for site in netbox.dcim.get_sites())
	existing_device_roles = set(device_role['name'] for device_role in netbox.dcim.get_device_roles())
	existing_manufacturers = set(manufacturer['name'] for manufacturer in netbox.dcim.get_manufacturers())
	existing_device_types = set(device_type['model'] for device_type in netbox.dcim.get_device_types())
	existing_device_names = set(device['name'].strip() for device in netbox.dcim.get_devices() if device['name'])
	
	# Map netbox parent device name to the names of its device bays
	existing_device_bays = dict()

	for device_bay in netbox.dcim.get_device_bays():
		parent_name = device_bay['device']['name']
	
		if parent_name not in existing_device_bays:
			existing_device_bays[parent_name] = set()

		existing_device_bays[parent_name].add(device_bay['name'])
	
	not_created_parents = []
	for racktables_device_id,object_name,label,asset_no,comment in data:
		
		# Used for a child device whose parent isn't yet created and needs to be skipped
		not_created_parent = False

		# Some names in racktables have trailing or leading spaces
		if not object_name:
			print("No name for", racktables_device_id,object_name,label,asset_no,comment)
			continue

		object_name = object_name.strip()
		if object_name not in existing_device_names:
			# Create a "None" site, device type, role, manufacturer and finally device for this loose object 
			site_name = "None"
			
			manufacturer, device_role, device_type_model = get_manufacturer_role_type(cursor, racktables_device_id, objtype_id, 0, False)

			# print("Starting {}".format(object_name))

			if site_name not in existing_site_names:
				netbox.dcim.create_site(site_name, slugify(site_name))
				existing_site_names.add(site_name)
				print("Added non rack site", site_name)

			if device_role not in existing_device_roles:
				netbox.dcim.create_device_role(device_role,"ffffff",slugify(device_role))
				existing_device_roles.add(device_role)
				print("Added non rack device role", device_role)
			
			if manufacturer not in existing_manufacturers:
				netbox.dcim.create_manufacturer(manufacturer, slugify(manufacturer))
				existing_manufacturers.add(manufacturer)
				print("Added non rack manufacturer", manufacturer)

			is_child = False
			is_child_parent_id = None
			is_child_parent_name = None

			is_parent = False				

			# Check if the device is in a VM cluster and if so add it to that when creating it in Netbox
			device_in_vm_cluster, device_vm_cluster_name, parent_entity_ids = device_is_in_cluster(racktables_device_id)

			# Check if it is a child device that needs to be created with a child device type then created marked as mounted inside a parent device's device bay.
			# The parent device might not exist yet, in which case it is skipped and retried after 
			for parent_from_pairs_objtype_id, child_from_pairs_objtype_id in parent_child_objtype_id_pairs:
				
				# Server that might reside in a server chassis
				if objtype_id == child_from_pairs_objtype_id:
					
					# Got a parent id, so check that it is a Server Chassis and if so, create the device type with child and later add a device bay to that parent object with this newly created child Server object
					for parent_entity_id in parent_entity_ids:

						cursor.execute("SELECT objtype_id,name FROM Object WHERE id={};".format(parent_entity_id))
						parent_objtype_id,parent_name = cursor.fetchall()[0]
						
						if parent_objtype_id == parent_from_pairs_objtype_id:

							parent_name = parent_name.strip()
							is_child_parent_id = netbox.dcim.get_devices(name=parent_name)
							
							# The parent is not yet created, so break creating this device and come back later
							if not is_child_parent_id:
								not_created_parents.append((racktables_device_id,object_name,label,asset_no,comment))
								not_created_parent = True
								break
							else:
								is_child_parent_id = is_child_parent_id[0]['id']

							is_child_parent_name = parent_name
							is_child = True
							# print("{} child".format(object_name))
							break
					
					if is_child:
						break

				# Could be a loose patch panel that has child devices
				if objtype_id == parent_from_pairs_objtype_id and not not_created_parent:
					cursor.execute("SELECT child_entity_id FROM EntityLink WHERE parent_entity_type=\"object\" AND parent_entity_id={};".format(racktables_device_id))
					child_entity_ids = cursor.fetchall()

					# print(child_entity_ids)

					for child_entity_id in [x[0] for x in child_entity_ids]:
						cursor.execute("SELECT objtype_id,name FROM Object WHERE id={};".format(child_entity_id))
						child_objtype_id,child_name = cursor.fetchall()[0]

						# print(child_objtype_id, child_name)

						if child_objtype_id == child_from_pairs_objtype_id:
							is_parent = True
							# print("{} parent".format(object_name))
							break
					
					if is_parent:
						break

			# Continue to next device, skipping the child with no parent yet
			if not_created_parent:
				continue

			subdevice_role = ""

			if is_child:
				device_type_model += "-child"
				subdevice_role = "child"

			if is_parent:
				device_type_model += "-parent"
				subdevice_role = "parent"

			if device_type_model not in existing_device_types:

				netbox.dcim.create_device_type(model=device_type_model,slug=slugify(device_type_model), manufacturer={"name":manufacturer},u_height=0,subdevice_role=subdevice_role)
				existing_device_types.add(device_type_model)

			device_tags = getTags(cursor = cursor, entity_realm="object", entity_id = racktables_device_id)
			custom_fields = get_custom_fields(cursor, racktables_device_id, {"Device_Label": label})
			serial = serials[racktables_device_id] if racktables_device_id in serials else ""
			
			asset_no = asset_no.strip() if asset_no else None
			if asset_no and asset_no in asset_tags:
				asset_no = asset_no+ "-1"

			# print("Creating device \"{}\"".format(object_name), device_type_model, device_role, manufacturer, site_name, asset_no)		
			added_device = netbox.dcim.create_device(name=object_name,cluster={"name": device_vm_cluster_name} if device_in_vm_cluster else None,asset_tag=asset_no, serial=serial,custom_fields=custom_fields, device_type=device_type_model, device_role=device_role, site_name=site_name,comment=comment[:200] if comment else "",tags=device_tags)
			asset_tags.add(asset_no)

			# Later used for creating interfaces
			global_non_physical_object_ids.add((object_name, racktables_device_id, added_device['id'], objtype_id))

			# If device was a child device mounted inside a physically mounted parent device, then create a device bay relating to the parent device filled with the just created item
			# Only one device can be assigned to each bay, so find the first open device bay name for the parent device, then use try and except to add the added device to it, although it should not fail since the child device was just created above
			if is_child:

				# Check that this parent object currently has any device bays in it, 
				if is_child_parent_name in existing_device_bays:
					new_bay_name = "bay-" + str(max([int(device_bay_name[len("bay-"):]) for device_bay_name in existing_device_bays[is_child_parent_name]]) + 1)
				else:
					existing_device_bays[is_child_parent_name] = set()
					new_bay_name = "bay-1"

				# print(new_bay_name, is_child_parent_name)
				existing_device_bays[is_child_parent_name].add(new_bay_name)

				netbox.dcim.create_device_bay(new_bay_name, device_id=is_child_parent_id, installed_device_id=added_device['id'])

	return not_created_parents

def change_interface_name(interface_name, objtype_id):
	interface_name = interface_name.strip()

	global interface_name_mappings
	
	if objtype_id in (7, 8):
		for prefix in interface_name_mappings:
			# Make sure the prefix is followed by a number so Etherent doesn't become Etherneternet
			if interface_name.startswith(prefix) and len(interface_name) > len(prefix) and interface_name[len(prefix)] in "0123456789- ":
				new_interface_name = interface_name.replace(prefix, interface_name_mappings[prefix], 1)
				
				# with open("prefixes", "a") as file:
					# file.write("{} => {}\n".format(interface_name, new_interface_name))

				interface_name = new_interface_name

	return interface_name

with connection.cursor() as cursor:
	
	# For the HW Type field: use this as the base name for the device type

	cursor.execute("SELECT object_id,string_value FROM AttributeValue WHERE attr_id=10014")
	for object_id,string_value in cursor.fetchall():
		serials[object_id] = string_value if string_value else ""

	# Turn the uint_value for attr_id 2 in table "AttributeValue" into a string from the table "Dictionary"
	cursor.execute("SELECT dict_key,dict_value FROM Dictionary")
	for dict_key,dict_value in cursor.fetchall():
		hw_types[dict_key] = dict_value.strip("[]").split("|")[0].strip().replace("%"," ")

	# Map the racktables id to the name to add to custom fields later
	cursor.execute("SELECT id,type,name FROM Attribute")
	yellow_attributes = cursor.fetchall()
	for Id,Type,name in yellow_attributes:
		slugified_attributes[Id] = name.replace(" ","_").replace("#","").replace(",","").replace("/","").replace(".","").strip("_")

# 		print("""{}:
#   type: {}
#   required: false
#   weight: 0
#   on_objects:
#   - dcim.models.Device""".format(slugified_attributes[Id], {"string": "text", "uint":"integer", "date":"text", "float":"integer","dict":"text"}[Type]))

# 	print("\n\nPaste that in the intializers/custom_fields.yml file for this program to work!")
	print("Make sure to also set the page limit to 0 in the conf.env file")

	# Create all the tags
	global_tags = set(tag['name'] for tag in netbox.extras.get_tags())

	IPV4_TAG = "IPv4"
	IPV6_TAG = "IPv6"

	create_global_tags((IPV6_TAG, IPV4_TAG))
	cursor.execute("SELECT tag FROM TagTree;")
	create_global_tags(tag[0] for tag in cursor.fetchall())

	print("Created tags")


	# Map the vlan id domain to the name
	vlan_domain_id_names = dict()

	existing_vlan_groups = set()

	for vlan_group in netbox.ipam.get_vlan_groups():
		existing_vlan_groups.add(vlan_group['name'])

	if CREATE_VLAN_GROUPS:
		print("Creating VLAN Groups")
		cursor.execute("SELECT id,description FROM VLANDomain")
		vlans_domains = cursor.fetchall()
		for Id, description in vlans_domains:

			vlan_domain_id_names[Id] = description

			if description not in existing_vlan_groups:
				netbox.ipam.create_vlan_group(name=description, slug=slugify(description), custom_fields= {"VLAN_Domain_ID":Id})
				existing_vlan_groups.add(description)

	# Map the racktables network id to the vlan group and vlan names
	network_id_group_name_id = pickleLoad('network_id_group_name_id', dict())

	if CREATE_VLANS:
		print("Creating VLANs")

		vlans_for_group = dict()
		for IP in ("4", "6"):
			cursor.execute("SELECT domain_id,vlan_id,ipv{}net_id FROM VLANIPv{}".format(IP, IP))
			vlans = cursor.fetchall()
			for domain_id,vlan_id,net_id in vlans:
				
				cursor.execute("SELECT vlan_descr FROM VLANDescription WHERE domain_id={} AND vlan_id={}".format(domain_id,vlan_id))
				vlan_name = cursor.fetchall()[0][0]
				
				if not vlan_name:
					continue

				vlan_group_name = vlan_domain_id_names[domain_id]
				if vlan_group_name not in vlans_for_group:
					vlans_for_group[vlan_group_name] = set()
				
				name = vlan_name
				# Need to get a unique name for the vlan_name if it is already in this group
				if name in vlans_for_group[vlan_group_name]:
					counter = 1
					while True:
						name = vlan_name+"-"+str(counter)
						if name not in vlans_for_group[vlan_group_name]:
							break
						else:
							counter += 1
				

				# print(vlan_group_name, vlan_id, name)
				try:
					created_vlan = netbox.ipam.create_vlan(group={"name":vlan_group_name},vid=vlan_id,vlan_name=name)
					network_id_group_name_id[net_id] = (vlan_group_name, vlan_name, created_vlan['id'])
					# print("created", vlan_group_name,vlan_id,name)

				except:
					print(vlan_group_name,vlan_id,name)
					print("Something went wrong here\n\n")
				
				vlans_for_group[vlan_group_name].add(name)
	
		pickleDump('network_id_group_name_id', network_id_group_name_id)


	print("About to create Clusters and VMs")
	if CREATE_MOUNTED_VMS:
		# Create VM Clusters and the VMs that exist in them
		existing_cluster_types = set(cluster_type['name'] for cluster_type in netbox.virtualization.get_cluster_types())
		existing_cluster_names = set(cluster['name'] for cluster in netbox.virtualization.get_clusters())
		existing_virtual_machines = set(virtual_machine['name'] for virtual_machine in netbox.virtualization.get_virtual_machines())

		# print("Got {} existing virtual machines".format(len(existing_virtual_machines)))

		vm_counter = 0
		cursor.execute("SELECT id,name,asset_no,label FROM Object WHERE objtype_id=1505;")
		clusters = cursor.fetchall()
		for Id, cluster_name, asset_no,label in clusters:
			
			if cluster_name not in existing_cluster_types:
				netbox.virtualization.create_cluster_type(cluster_name,  slugify(cluster_name))
				existing_cluster_types.add(cluster_name)

			if cluster_name not in existing_cluster_names:
				netbox.virtualization.create_cluster(cluster_name, cluster_name)
				existing_cluster_names.add(cluster_name)

			# Create all the VMs that exist in this cluster and assign them to this cluster
			cursor.execute("SELECT child_entity_type,child_entity_id FROM EntityLink WHERE parent_entity_id={};".format(Id))
			child_virtual_machines = cursor.fetchall()
			for child_entity_type,child_entity_id in child_virtual_machines:

				cursor.execute("SELECT name,label,comment,objtype_id,asset_no FROM Object WHERE id={};".format(child_entity_id))
				virtual_machine_name, virtual_machine_label, virtual_machine_comment, virtual_machine_objtype_id,virtual_machine_asset_no = cursor.fetchall()[0]

				# Confirm that the child is VM and not a server or other to not create duplicates
				if virtual_machine_objtype_id != 1504 or not virtual_machine_name:
					continue

				virtual_machine_name = virtual_machine_name.strip()

				if virtual_machine_name not in existing_virtual_machines:
					virtual_machine_tags = getTags(cursor, "object", child_entity_id)

					netbox.virtualization.create_virtual_machine(virtual_machine_name, cluster_name, tags=virtual_machine_tags, comments=virtual_machine_comment[:200] if virtual_machine_comment else "",custom_fields= {"VM_Label": virtual_machine_label[:200] if virtual_machine_label else "", "VM_Asset_No": virtual_machine_asset_no if virtual_machine_asset_no else ""})
					existing_virtual_machines.add(virtual_machine_name)
					# print("Created", virtual_machine_name)
				
				else:
					# print(virtual_machine_name, "exists")
					pass
				
				vm_counter += 1
				# print(virtual_machine_name, vm_counter)

	if CREATE_UNMOUNTED_VMS:
		print("Creating unmounted VMs")

		# Create the VMs that are not in clusters
		unmounted_cluster_name = "Unmounted Cluster"
		if unmounted_cluster_name not in existing_cluster_types:
			netbox.virtualization.create_cluster_type(unmounted_cluster_name,  slugify(unmounted_cluster_name))
		
		if unmounted_cluster_name not in existing_cluster_names:
			netbox.virtualization.create_cluster(unmounted_cluster_name, unmounted_cluster_name)

		cursor.execute("SELECT name,label,comment,objtype_id,asset_no FROM Object WHERE objtype_id=1504;")
		vms = cursor.fetchall()
		for virtual_machine_name, virtual_machine_label, virtual_machine_comment, virtual_machine_objtype_id, virtual_machine_asset_no in vms:

			if virtual_machine_objtype_id != 1504 or not virtual_machine_name:
				continue
			
			virtual_machine_name = virtual_machine_name.strip()

			if virtual_machine_name not in existing_virtual_machines:
				virtual_machine_tags = getTags(cursor, "object", child_entity_id)

				netbox.virtualization.create_virtual_machine(virtual_machine_name, unmounted_cluster_name, tags=virtual_machine_tags, comments=virtual_machine_comment[:200] if virtual_machine_comment else "", custom_fields={"VM_Label": virtual_machine_label[:200] if virtual_machine_label else "", "VM_Asset_No": virtual_machine_asset_no if virtual_machine_asset_no else ""})
				existing_virtual_machines.add(virtual_machine_name)

			else:
				# print(virtual_machine_name, "exists")
				pass
					
			vm_counter += 1


	# Map interface integer type to the string type
	cursor.execute("SELECT id,oif_name FROM PortOuterInterface;")
	PortOuterInterfaces = dict()
	for k,v in cursor.fetchall():
		PortOuterInterfaces[k] = v
	
	# Fill racks with physical devices
	if CREATE_RACKED_DEVICES:
		
		print("Creating sites, racks, and filling rack space")


		global_devices = netbox.dcim.get_devices()
		print("Got {} devices".format(len(global_devices)))

		global_names = set(device['name'] for device in global_devices)
		print("Got {} names".format(len(global_names)))	

		global_manufacturers = set(manufacturer['name'] for manufacturer in netbox.dcim.get_manufacturers())
		print("Got {} manufacturers".format(len(global_manufacturers)))

		global_device_roles = set(device_role['name'] for device_role in netbox.dcim.get_device_roles())
		print("Got {} device roles".format(len(global_device_roles)))

		global_device_types = set(device_type['model'] for device_type in netbox.dcim.get_device_types())
		print("Got {} device types".format(len(global_device_types)))

		cursor.execute("SELECT id,name,label,asset_no,comment FROM Object WHERE objtype_id=1562")
		sites = cursor.fetchall()
		for site_id, site_name, site_label, site_asset_no, site_comment in sites:

			if not netbox.dcim.get_sites(name=site_name) or True:
				
				if len(site_name) > SITE_NAME_LENGTH_THRESHOLD:
					print("This is probably a location (address)", site_name)
					try:
						# Create location
						pass
					except:
						# Location exists
						pass

					continue

				print("Creating site (datacenter)", site_name,"\n")

				try:
					netbox.dcim.create_site(site_name, slugify(site_name))
				except:
					print("Failed to create site", site_name)
					pass

				for row_id, row_name, row_label, row_asset_no, row_comment in getRowsAtSite(cursor, site_id):
					for rack_id, rack_name, rack_label, rack_asset_no, rack_comment in getRacksAtRow(cursor,row_id):
						# Get rack height from table AttributeValue where attr_id=27, object_id is rack, uint_value is the height
						rack_tags = getTags(cursor, "rack", rack_id)
						rack_height = getRackHeight(cursor, rack_id)

						atoms = getAtomsAtRack(cursor, rack_id)
						
						# Make sure rack name does not already contain row
						if not rack_name.startswith(row_name.rstrip(".") + "."):
							rack_name = site_name + "." + row_name + "." + rack_name 
						else:
							rack_name = site_name + "." + rack_name
						
						# Racks do NOT require a unique name, but they are given one by this script.
						# Otherwise get_racks() based on name only would be wrong to use
						rack = netbox.dcim.create_rack(name=rack_name,comment=rack_comment[:200] if rack_comment else "",site_name=site_name,u_height=rack_height,tags=rack_tags)

						createObjectsInRackFromAtoms(cursor, atoms, rack_name, rack['id'])

		pickleDump("global_physical_object_ids", global_physical_object_ids)

		# Get all the object_id	from table Rackspace for the object_id in the table Port for the name and id
		# Use type as id to get oif_name in table PortOuterInterface
		# Use id as porta or portb in Link table to get the parent/linked object
	
	else:
		global_physical_object_ids = pickleLoad("global_physical_object_ids", set())

	# Create non racked device, some of which required the physical devices above as parents
	print("\n\nAbout to create non racked devices")
	
	# Load from file and later save new additions to file. This avoids querying netbox for a racktables device_id which it does not store
	global_non_physical_object_ids = pickleLoad("global_non_physical_object_ids", set())

	if CREATE_NON_RACKED_DEVICES:

		# Map netbox parent device name to the names of its device bays
		existing_device_bays = dict()

		for device_bay in netbox.dcim.get_device_bays():
			parent_name = device_bay['device']['name']
			
			if parent_name not in existing_device_bays:
				existing_device_bays[parent_name] = set()

			existing_device_bays[parent_name].add(device_bay['name'])

		for objtype_id in objtype_id_names:
			print("\n\nobjtype_id {} {}\n\n".format(objtype_id, objtype_id_names[objtype_id]))

			# Get all objects of that objtype_id and try to create them if they do not exist
			cursor.execute("SELECT id,name,label,asset_no,comment FROM Object WHERE objtype_id={}".format(objtype_id))
			objs = cursor.fetchall()
			children_without_parents = create_parent_child_devices(cursor, objs, objtype_id)
			
			# Try to recreate the children devices that didn't have parents before
			if children_without_parents:
				create_parent_child_devices(cursor, children_without_parents, objtype_id)

		pickleDump("global_non_physical_object_ids", global_non_physical_object_ids)

	# Names for device to see if that interface already exists for the device with fast lookup
	interface_local_names_for_device = dict()
	
	# netbox Id of the interface mapped to 
	interface_netbox_ids_for_device = dict()

	# This will probably take a while for about 50,000 physical interfaces
	if CREATE_INTERFACES:
		print("Getting interfaces")
		start_time = time.time()

		for value in get_interfaces():
			racktables_device_id = value['device']['id']

			if racktables_device_id not in interface_local_names_for_device:
				interface_local_names_for_device[racktables_device_id] = set()

			interface_local_names_for_device[racktables_device_id].add(value['name'])

			if racktables_device_id not in interface_netbox_ids_for_device:
				interface_netbox_ids_for_device[racktables_device_id] = dict()

			interface_netbox_ids_for_device[racktables_device_id][value['name']] = value['id']

		print("Got {} interfaces in {} seconds".format(sum(len(interface_local_names_for_device[device_id]) for device_id in interface_local_names_for_device), time.time() - start_time))
		
		# Store the SQL id and the netbox interface id to later create the connection between the two from the Link table
		connection_ids = dict()

		interface_counter = 0
		print("Creating interfaces for devices")
		for device_list in (global_physical_object_ids, global_non_physical_object_ids):
			for device_name, racktables_object_id, netbox_id, objtype_id in device_list:

				# print(device_name, racktables_object_id, netbox_id)

				cursor.execute("SELECT id,name,iif_id,type,label FROM Port WHERE object_id={}".format(racktables_object_id))
				ports = cursor.fetchall()

				if netbox_id not in interface_local_names_for_device:
					interface_local_names_for_device[netbox_id] = set()
				
				if netbox_id not in interface_netbox_ids_for_device:
					interface_netbox_ids_for_device[netbox_id] = dict()

				for Id, interface_name, iif_if, Type, label in ports:

					PortOuterInterface = PortOuterInterfaces[Type]

					if interface_name:
						interface_name = change_interface_name(interface_name, objtype_id)
					else:
						continue

					# Create regular interface, which all things need to be to create connections accross devices
					if interface_name not in interface_local_names_for_device[netbox_id]:
						
						if not interface_name:
							print("No interface_name", Id,"\n\n\n")
							continue
						if not netbox_id:
							print("No netbox_id", netbox_id,"\n\n\n")
							continue
						if not PortOuterInterface:
							print("No PortOuterInterface", PortOuterInterface,"\n\n\n")
							continue

						added_interface = netbox.dcim.create_interface(name=interface_name, interface_type="other", device_id=netbox_id, custom_fields= {"Device_Interface_Type": PortOuterInterface}, label=label[:200] if label else "")
						
						interface_local_names_for_device[netbox_id].add(interface_name)
						interface_netbox_ids_for_device[netbox_id][interface_name] = added_interface['id']

						# Link racktables interface id to netbox interface id
						connection_ids[Id] = added_interface['id']

					else:
						print(Id, interface_name, "exists")

						# Link racktables interface id to netbox interface id based on the local name.
						connection_ids[Id] = interface_netbox_ids_for_device[netbox_id][interface_name]


					interface_counter += 1
					if interface_counter % 500 == 0:
						print("Created {} interfaces".format(interface_counter))
		
		pickleDump('connection_ids', connection_ids)


	# The "interfaces" created from the IP addresses below don't need connections made because they are "IP Addresses" in RT, whereas connections are made for "ports and links" which was done before

	# Create interface connections
	if CREATE_INTERFACE_CONNECTIONS:
		print("Creating interface connections")
		connection_ids = pickleLoad('connection_ids', dict())

		# Create the interface connections based on racktable's Link table's storage of
		cursor.execute("SELECT porta,portb,cable FROM Link")
		connections = cursor.fetchall()

		for interface_a, interface_b, cable in connections:
			# These error are fixed by including more objtype_ids in the global list for non racked devices
			if interface_a not in connection_ids:
				print("ERROR", interface_a, "a not in")
				continue
			
			if interface_b not in connection_ids:
				print("ERROR", interface_b, "b not in")
				continue

			netbox_id_a = connection_ids[interface_a]
			netbox_id_b = connection_ids[interface_b]

			try:
				netbox.dcim.create_interface_connection(netbox_id_a, netbox_id_b, 'dcim.interface', 'dcim.interface')
			except:
				error_log("Interface connection error {} {}".format(netbox_id_a, netbox_id_b))


	device_names = dict()
	cursor.execute("SELECT id,name FROM Object")	
	for Id,device_name in cursor.fetchall():
		if not device_name:
			continue
		device_names[Id] = device_name.strip()
	
	existing_prefixes = set(prefix['prefix'] for prefix in netbox.ipam.get_ip_prefixes())
	existing_ips = set(prefix['address'] for prefix in netbox.ipam.get_ip_addresses())


	versions = []
	if CREATE_IPV4:
		versions.append("4")
	if CREATE_IPV6:
		versions.append("6")

	for IP in versions:
		print("\n\nCreating IPv{}s Networks\n\n".format(IP))
		cursor.execute("SELECT id,ip,mask,name,comment FROM IPv{}Network".format(IP))
		ipv46Networks = cursor.fetchall()

		for Id,ip,mask,prefix_name,comment in ipv46Networks if CREATE_IP_NETWORKS else []:
			
			# Skip the single IP addresses
			if (IP == "4" and mask == 32) or (IP == "6" and mask == 128): 
				continue
			
			prefix = str(ipaddress.ip_address(ip)) + "/" + str(mask)
			
			if prefix in existing_prefixes:
				continue
			
			if Id in network_id_group_name_id:
				vlan_name = network_id_group_name_id[Id][1]
				vlan_id = network_id_group_name_id[Id][2]
			else:
				vlan_name = None

			tags = getTags(cursor, "ipv{}net".format(IP), Id)

			# print("Creaing {} {} in vlan {}".format(prefix, prefix_name, vlan_name))
			
			# Description takes at most 200 characters
			netbox.ipam.create_ip_prefix(vlan={"id":vlan_id} if vlan_name else None,prefix=prefix,description=comment[:200] if comment else "",custom_fields={'Prefix_Name': prefix_name},tags = [{'name': IPV4_TAG if IP == "4" else IPV6_TAG}] + tags)


		print("Creating IPv{} Addresses".format(IP))
		cursor.execute("SELECT ip,name,comment FROM IPv{}Address".format(IP))
		ip_addresses = cursor.fetchall()
		ip_names_comments = dict([(ip, (name, comment)) for ip,name,comment in ip_addresses])
		# print(ip_names_comments)
		
		# These IPs are the ones allocated to devices, not ones that are only reserved
		cursor.execute("SELECT ALO.object_id,ALO.ip,ALO.name,ALO.type,OBJ.objtype_id,OBJ.name FROM IPv{}Allocation ALO, Object OBJ WHERE OBJ.id=ALO.object_id".format(IP))
		ip_allocations = cursor.fetchall()

		for object_id,ip,interface_name,ip_type,objtype_id,device_name in ip_allocations if CREATE_IP_ALLOCATED else []:

			if ip in ip_names_comments:
				ip_name, comment = ip_names_comments[ip]
			else:
				ip_name, comment = "", ""

			if device_name:
				device_name = device_name.strip()
			else:
				continue

			
			string_ip = str(ipaddress.ip_address(ip)) + "{}".format("/32" if IP == "4" else "")
			if string_ip in existing_ips and ip_type != "shared":
				continue
			else:
				existing_ips.add(string_ip)

			use_vrrp_role = "vrrp" if ip_type == "shared" else None

			if interface_name:
				interface_name = change_interface_name(interface_name.strip(), objtype_id)
			else:
				interface_name = "no_RT_name"+str(random.randint(0,99999))


			# Check through the interfaces that exist for this device in netbox, created previously
			# If one exists with the same name as the IP has in racktables, add the ip to that
			# Else create a dummy virtual interface with the new name and add the ip to that interface
			# because Racktables allows you to give IP interfaces any name not necessarily one of the existing interfaces explicitly set as interfaces
			if objtype_id == 1504:
				device_or_vm = "vm"
				interface_list = netbox.virtualization.get_interfaces(virtual_machine=device_name)
			else:
				device_or_vm = "device"
				interface_list = netbox.dcim.get_interfaces(device=device_name)

			# print(device_name)

			device_contained_same_interface = False
			for name,interface_id in [(interface['name'], interface['id']) for interface in interface_list]:
				
				if interface_name == name:

					netbox.ipam.create_ip_address(address=string_ip,role=use_vrrp_role,assigned_object={'device'if device_or_vm == "device" else "virtual_machine":device_name},interface_type="virtual",assigned_object_type="dcim.interface" if device_or_vm == "device" else "virtualization.vminterface",assigned_object_id=interface_id,description=comment[:200] if comment else "",custom_fields={'IP_Name': ip_name,'Interface_Name':interface_name,'IP_Type':ip_type},tags=[{'name': IPV4_TAG if IP == "4" else IPV6_TAG}])
					
					device_contained_same_interface = True
					break

			if not device_contained_same_interface:

				if device_or_vm == "device":
					device_id = netbox.dcim.get_devices(name=device_name)[0]['id']
				else:
					device_id = netbox.virtualization.get_virtual_machines(name=device_name)[0]['id']

				# print("Creating dummy {} virtual interface {} for {} and {}".format(device_or_vm, interface_name, device_name, string_ip))
				# Because there is no way to access interfaces per device without querying the whole list of interfaces, do a try and except for iterating over the name
				# An error would occur when there are duplicate IP interface names in RT
				try:
					if device_or_vm == "device":
						added_interface = netbox.dcim.create_interface(name=interface_name,interface_type="virtual",device_id=device_id, custom_fields={"Device_Interface_Type": "Virtual"})
					else:
						added_interface = netbox.virtualization.create_interface(name=interface_name,interface_type="virtual",virtual_machine=device_name,custom_fields={"VM_Interface_Type": "Virtual"})
				except:
					# Probably had a name colision with interface_name
					print("ERROR \n\n")
					pass
				
				else:
					# Make sure ip is not already on this interface?
					netbox.ipam.create_ip_address(address=string_ip,role=use_vrrp_role,assigned_object_id=added_interface['id'],assigned_object={"device" if device_or_vm == "device" else "virtual_machine" :{'id': device_id}},interface_type="virtual",assigned_object_type="dcim.interface" if device_or_vm == "device" else "virtualization.vminterface",description=comment[:200] if comment else "",custom_fields={'IP_Name': ip_name, 'Interface_Name': interface_name, 'IP_Type': ip_type},tags = [{'name': IPV4_TAG if IP == "4" else IPV6_TAG}])

		# Add ip without any associated device
		for ip in ip_names_comments if CREATE_IP_NOT_ALLOCATED else []:
			string_ip = str(ipaddress.ip_address(ip)) + "{}".format("/32" if IP == "4" else "")
			if string_ip in existing_ips:
				continue
			ip_name, comment = ip_names_comments[ip]
			netbox.ipam.create_ip_address(address=string_ip,description=comment[:200] if comment else "",custom_fields={'IP_Name': ip_name},tags=[{'name': IPV4_TAG if IP == "4" else IPV6_TAG}])

	




