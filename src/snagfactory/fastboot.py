import os

DEFAULT_FB_BUFFER_SIZE = 0x7000000
MMC_LBA_SIZE = 512

class FastbootArgs:
	def __init__(self, d):
		for key, value in d.items():
			setattr(self, key, value)

def flash_huge_image(board, part_name: str, fb_buffer_size: int, image: str, offset):
	"""
	Flash an image that doesn't fit inside the Fastboot RAM buffer.
	This is done by flashing the image in sections. Each section has
	to be written to a specific offset in the storage device. To
	achieve this, temporary Fastboot partition aliases are used.
	"""

	cmds = []
	file_size = os.path.getsize(image)

	nchunks = file_size // fb_buffer_size
	remainder = file_size % fb_buffer_size

	if offset is None:
		cmds.append(f'oem_run:gpt setenv mmc {board.config["device-num"]} {part_name} ')
	else:
		if offset % MMC_LBA_SIZE != 0:
			raise ValueError(f"offset {offset} is not aligned with a {MMC_LBA_SIZE}-byte LBA!")

		cmds.append(f'oem_run:setenv gpt_partition_addr {(offset // MMC_LBA_SIZE):x}')

	for i in range(0, nchunks):
		# setexpr interprets every number as a hexadecimal value
		# I've added '0x' prefixes just in case this changes for some reason
		cmds.append('oem_run:setexpr snag_offset 0x${gpt_partition_addr} + ' + f'0x{((i * fb_buffer_size) // MMC_LBA_SIZE):x}')
		cmds.append('oem_run:setenv fastboot_raw_partition_temp 0x${snag_offset}' f' 0x{fb_buffer_size:x}')
		cmds.append(f'download:{image}#{i * fb_buffer_size}:{fb_buffer_size}')
		cmds.append("flash:temp")

	if remainder > 0:
		cmds.append('oem_run:setexpr snag_offset 0x${gpt_partition_addr} + ' + f'0x{((nchunks * fb_buffer_size) // MMC_LBA_SIZE):x}')
		cmds.append('oem_run:setenv fastboot_raw_partition_temp 0x${snag_offset}' f' 0x{remainder}')
		cmds.append(f'download:{image}#{nchunks * fb_buffer_size}:{remainder}')
		cmds.append("flash:temp")

	return cmds

def flash_image_to_part(board, image: str, part, part_start = None):
	fb_buffer_size = board.config.get("fb_buffer_size", DEFAULT_FB_BUFFER_SIZE)

	if fb_buffer_size % MMC_LBA_SIZE != 0:
		raise ValueError(f"Specified fb_buffer_size is invalid! Must be a multiple of {MMC_LBA_SIZE}")

	file_size = os.path.getsize(image)

	if file_size % MMC_LBA_SIZE != 0:
		raise ValueError(f"File {image} has an invalid size! Must be a multiple of {MMC_LBA_SIZE}")

	if file_size > fb_buffer_size:
		return flash_huge_image(board, part, fb_buffer_size, image, part_start)

	cmds = [f'download:{image}']

	if isinstance(part, str):
		cmds.append(f"flash:{part}")
	else:
		cmds.append(f"flash:{board.config['device-num']}:{part}")

	return cmds

def flash_partition_table(device_num: int, partitions: list):
	partitions_env = ""

	for partition in partitions:
		if "size" not in partition or "name" not in partition:
			raise ValueError("Invalid partition table entry found in batch file, partition size and name must be specified!")

		for key, value in partition.items():
			if key == "image":
				continue

			partitions_env += f"{key}={value},"

		partitions_env = partitions_env.rstrip(",") + ";"

	return ["oem_run:setenv partitions " + "'" + partitions_env + "'", "oem_format", f"oem_run:part list mmc {device_num}"]

def convert_from_suffix_int(n):
	return 1048576 * int(n[:-1]) if "M" in n else int(n)

def flash_partition_images(board):
	part_index = 1

	cmds = []

	for partition in board.config["partitions"]:
		if "image" not in partition:
			continue

		if "name" in partition:
			part_name = partition["name"]
		else:
			part_name = part_index

		cmds += flash_image_to_part(board, partition["image"], part_name)

	return cmds

def emmc_flash_bootpart(board, config: dict):
	return [f"download:{config['image']}", f"flash:{config['name']}"]

def get_fastboot_args(board):
	args = {
		"loglevel": "info",
		"timeout": 60000,
		"port": board.path,
		"factory": True,
		"fastboot_cmd": [],
	}

	if "boot0" in board.config:
		args["fastboot_cmd"] += emmc_flash_bootpart(board, board.config["boot0"])

	if "boot1" in board.config:
		args["fastboot_cmd"] += emmc_flash_bootpart(board, board.config["boot1"])

	if "image" in board.config and "partitions" in board.config:
		raise ValueError("Invalid batch configuration file: specify either 'image' or 'partitions' for one soc family, not both!")
	elif "image" in board.config:
		args["fastboot_cmd"] += flash_image_to_part(board, board.config["image"], 0, 0)
	elif "partitions" in board.config:
		args["fastboot_cmd"] += flash_partition_table(board.config["device-num"], board.config["partitions"])
		args["fastboot_cmd"] += flash_partition_images(board)
	else:
		raise ValueError("Invalid batch configuration file: specify either 'image' or 'partitions' for each soc family!")

	if "post-flash" in board.config:
		for cmd in board.config["post-flash"]:
			args["fastboot_cmd"].append(cmd)

	print("\n".join(args["fastboot_cmd"]))

	return FastbootArgs(args)

