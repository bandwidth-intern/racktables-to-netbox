# For each IP network if the tags are in ip_zone_tags add up available ips in that zone

import ipaddress
from netbox import NetBox

nb_host = 
nb_port = 8000
nb_token = '0123456789abcdef0123456789abcdef01234567'
ip_zone_tags = 

netbox = NetBox(host=nb_host, port=nb_port, use_ssl=False, auth_token=nb_token)




free_per_tag = dict(zip(ip_zone_tags, [set() for a in range(0,len(ip_zone_tags))]))

# Only ipv4 for now
ip_addresses = netbox.ipam.get_ip_addresses(netbox.ipam.get_ip_addresses(tag="ipv4"))
print("Got IP addresses from Netbox")
ip_prefixes = netbox.ipam.get_ip_prefixes(tag="ipv4")
print("Got IP prefixes from Netbox")

total_len = 0

for prefix, tags in [(prefix['prefix'], [tag['name'] for tag in prefix['tags']]) for prefix in ip_prefixes]:

	network_list = set(str(ip)+"/32" for ip in ipaddress.IPv4Network(prefix))

	total_len += len(network_list)

	for ip in ip_addresses:
		network_list.discard(ip['address'])

	for tag in tags:
		if tag in ip_zone_tags:
			free_per_tag[tag].update(network_list)

print("Total IP addresses with these tags:", total_len)
print("Free addresses per tag, based on tags on prefixes:")
for tag, ip_set in free_per_tag.items():
	print("{}:".format(tag), len(ip_set))

