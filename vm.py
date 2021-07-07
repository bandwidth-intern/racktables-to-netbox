# Update uniquely named VMs in Netbox with cpu, disk and memory information from RHEVM instances
import bs4
import requests
from netbox import NetBox
nb_host = 
nb_port = 8000
nb_token = '0123456789abcdef0123456789abcdef01234567'
# Make sure this is URL encoded, "@" = "$40", " " = "%20"
rhev_username = ""
rhev_password = ""
rhev_pem = ""
rhev_urls = []


netbox = NetBox(host=nb_host, port=nb_port, use_ssl=False, auth_token=nb_token)

for url in rhev_urls:
	print("\nTrying", url)
	try:
		post=requests.post(url+"/ovirt-engine/sso/oauth/token?grant_type=password&scope=ovirt-app-api&username="+rhev_username+"&password="+rhev_password,timeout=2,verify=rhev_pem,headers={"Accept":"application/json","Content-Type":"application/x-www-form-urlencoded"})
	except Exception as e:
		if isinstance(e, requests.exceptions.ConnectTimeout):
			print("Timed out on", url)
			continue
		else:
			print(str(e))
			continue

	if post.status_code!=200:
		print(post.status_code)
		exit()
	print("Logged in successfully")

	get=requests.get(url+"/ovirt-engine/api/vms",verify=rhev_pem,headers={"Accept":"application/xml","Authorization":"Bearer "+post.content.decode().split("\"")[3]})
	if get.status_code!=200:
		print(get.status_code)
		exit()
	vms=bs4.BeautifulSoup(get.content.decode(),"html.parser").find_all("vm")

	get=requests.get(url+"/ovirt-engine/api/clusters",verify=rhev_pem,headers={"Accept":"application/xml","Authorization":"Bearer "+post.content.decode().split("\"")[3]})
	if get.status_code!=200:
		print(get.status_code)
		exit()
	clusters=bs4.BeautifulSoup(get.content.decode(),"html.parser").find_all("cluster")
	cluster_id_to_name = dict()
	for cluster in clusters:
		cluster_id_to_name[cluster['id']] = cluster.find('name').contents[0]

	get=requests.get(url+"/ovirt-engine/api/disks",verify=rhev_pem,headers={"Accept":"application/xml","Authorization":"Bearer "+post.content.decode().split("\"")[3]})
	if get.status_code!=200:
		print(get.status_code)
		exit()
	disks=bs4.BeautifulSoup(get.content.decode(),"html.parser").find_all("disk")
	disk_id_to_size=dict()
	for disk in disks:
		# Convert to GiB
		disk_id_to_size[disk['id']] = int(disk.find('provisioned_size').contents[0]) // 1024**3

	cluster_names = set()

	create_vms = []

	for vm in vms:
		vm_name = vm.find('name').contents[0]
		
		vm_description = vm.find('description').contents
		vm_description = vm_description[0] if vm_description else ""
		
		vm_cores = vm.find('cores').contents[0]
		vm_sockets = vm.find('sockets').contents[0]
		vm_cpus = int(vm_sockets)*int(vm_cores)

		# Convert to MiB
		vm_memory = int(vm.find('memory').contents[0]) // 1024**2

		cluster_id = vm.find('cluster')['id']
		vm_cluster_name = cluster_id_to_name[cluster_id]
		cluster_names.add(vm_cluster_name)

		vm_total_disk = 0
		vm_number_disks = 0

		for link in vm.find_all('link'):
			if 'diskattachments' in link['rel']:
				get=requests.get(url+link['href'],verify=rhev_pem,headers={"Accept":"application/xml","Authorization":"Bearer "+post.content.decode().split("\"")[3]})
				disk_attachments=bs4.BeautifulSoup(get.content.decode(),"html.parser").find_all("disk_attachment")
				for disk_attachment in disk_attachments:
					vm_total_disk += disk_id_to_size[disk_attachment.find('disk')['id']]
					vm_number_disks += 1
				break

		create_vms.append((vm_name, vm_description, vm_cpus, vm_memory, vm_total_disk, vm_cluster_name, []))

		nic_configurations = vm.find('nic_configurations')
		for nic_configuration in nic_configurations.find_all('nic_configuration') if nic_configurations else []:
			nic_name = nic_configuration.find('name').contents[0]
			nic_ip = nic_configuration.find('ip').find('address').contents[0]

			create_vms[-1][-1].append((nic_name, nic_ip))

	
	# Cluster names may be different in RHEVM than in Netbox, so skip this step unless a mapping is made
	# for cluster_name in cluster_names:
	# 	try:
	# 		netbox.virtualization.create_cluster_type(cluster_name,  slugify(cluster_name))
	# 		netbox.virtualization.create_cluster(cluster_name, cluster_name)
	# 	except:
	# 		pass

	# Look for VMs that exist in Netbox already (created from the Racktables dump) and update their cpu,ram,disk properties	
	# Netbox allows using the same VM name if they are not in the same cluster
	# If there are multiple with the same name, because there is no mapping between RHEVM and Racktables cluster names, they are ignored

	existing_vms = netbox.virtualization.get_virtual_machines()
	print("Got {} existing vms".format(len(existing_vms)))

	vm_ids = dict()
	for vm in existing_vms:
		if vm['name'] not in vm_ids:
			vm_ids[vm['name']] = []
		vm_ids[vm['name']].append(vm['id'])

	for vm_name in vm_ids:
		if len(vm_ids[vm_name]) > 1:
			vm_ids[vm_name] = None
		else:
			vm_ids[vm_name] = vm_ids[vm_name][0]

	# Only update existing VMs
	for vm_name, vm_description, vm_cpus, vm_memory, vm_total_disk, vm_cluster_name, vm_nics in create_vms:
		if vm_name not in vm_ids or vm_ids[vm_name] == None:
			continue

		url = "http://{}:{}/api/virtualization/virtual-machines/{}/".format(nb_host, nb_port, vm_ids[vm_name])
		headers = {"Authorization": "Token {}".format(nb_token), "Content-Type": "application/json"}
		data = '{{"memory": "{}", "disk": "{}", "vcpus": "{}"}}'.format(vm_memory, vm_total_disk, vm_cpus)
		requests.patch(url,headers=headers,data=data)
		print("Updated", vm_name)

		# created_vm = netbox.virtualization.create_virtual_machine(vm_name, vm_cluster_name, comments=vm_description[:200])
		# print("Created", vm_name)

		# Some nics have more than 1 IP address, so store the name to the interface id as they are created
		# created_vm_nics = dict()

		# for vm_nic_name, vm_nic_ip in vm_nics:
		# 	if vm_nic_name not in created_vm_nics:
		# 		print("Creating", vm_nic_name, vm_name)
		# 		added_interface = netbox.virtualization.create_interface(name=vm_nic_name,virtual_machine=vm_name,interface_type="virtual"})
		# 		created_vm_nics[vm_nic_name] = added_interface['id']				
		# 	netbox.ipam.create_ip_address(address=vm_nic_ip,assigned_object_id=created_vm_nics[vm_nic_name],assigned_object={'virtual_machine':{'id': created_vm['id']}},interface_type="virtual",assigned_object_type="virtualization.vminterface")



