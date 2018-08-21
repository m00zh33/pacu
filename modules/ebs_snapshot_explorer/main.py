#!/usr/bin/env python3
"""Module for ebs_snapshot_explorer"""
from copy import deepcopy
from botocore.exceptions import ClientError
from . import parser
from . import module_info


def get_interactive_instance(pacu, session, region):
    """Returns an instance given an AWS region
    Args:
        region (str): Region to get EC2 instance.
    Returns:
        str: The instance ID for the given region.
    """
    ec2_data = deepcopy(session.EC2)
    if 'Snapshots' not in ec2_data:
        fields = ['EC2', 'Snapshots']
        module = 'enum_ebs_volumes_snapshots'
        fetched_ec2_instances = pacu.fetch_data(fields, module)
        if fetched_ec2_instances is False:
            return None
    for instance in ec2_data['Instances']:
        if instance['Region'] == region:
            instance_id = instance['InstanceId']
            availability_zone = instance['Placement']['AvailabilityZone']
            return instance_id, availability_zone
    return None


def input_helper(input):
    """Helper function that loops until a successful response is given"""
    prompt = '    Load next set of volumes? (y/n) '
    while True:
        response = input(prompt)
        if response.lower() == 'y':
            return True
        elif response.lower() == 'n':
            return False

def load_volumes(client, print, input, instance_id, volume_ids):
    """Loads volumes on an instance.

    Args:
        client (boto3.client): client to interact with AWS
        print (func): Overwritten built-in print function
        input (func): Overwritten built-in input function
        instance_id (str): instance_id to attach volumes to
        volume_ids (list): list of volume_ids to attach to the instance.
    Returns:
        bool: True if all volumes were successfully attached.
    """

    # load volume set
    set_index = 0
    set_count = 10

    while set_index < len(volume_ids):
        current_volume_set = volume_ids[set_index:set_index + set_count]
        waiter = client.get_waiter('volume_available')
        waiter.wait(VolumeIds=current_volume_set)
        attached = modify_volume_set(
            client, print, 'attach_volume', instance_id, current_volume_set)
        if not attached:
            print(' Volume attachment failed')
            print(' Exiting...')
            return False
        running = input_helper(input)
        detached = modify_volume_set(
            client, print, 'detach_volume', instance_id, current_volume_set)
        if not detached:
            print(' Volume detachment failed')
            print(' Exiting...')
            return False
        waiter.wait(VolumeIds=current_volume_set)
        set_index += set_count
        if not running:
            break
    return True

def modify_volume_set(client, print, func, instance_id, volume_id_set):
    """Helper function to load volumes on an instance to not overload the
    instance.

    Args:
        client (boto3.client): client to interact with AWS
        print (func): Overwritten built-in print function
        func (str): Function sname to modify_volume_set
        instance_id (str): instance_id to attach volumes to
        volume_ids (list): list of volume_ids to (de)attach to the instance.
    Returns:
        bool: True if the volumes were successfully attached.
    """
    base_device = 'xvd'
    device_offset = 'f'
    for volume_id in volume_id_set:
        try:
            kwargs = {
                'Device':base_device+device_offset,
                'InstanceId':instance_id,
                'VolumeId':volume_id
            }
            caller = getattr(client, func)
            caller(**kwargs)
            device_offset = chr(ord(device_offset) + 1)
        except ClientError as error:
            code = error.response['Error']['Code']
            if  code == 'UnauthorizedOperation':
                print('  FAILURE MISSING AWS PERMISSIONS')
            elif code == 'InvalidAttachment.NotFound':
                print('  Skipping unattached volume...')
                continue
            elif error.response['Error']['Code'] == 'VolumeInUse':
                print('  Skipping Volume in Use...')
                continue
            else:
                print(error)
            return False
    return True


def get_snapshots(pacu, session, regions):
    """Returns snapshots given an AWS region
    Args:
        pacu (Main): Reference to Pacu
        session (PacuSession): Reference to the Pacu session database
        regions (list): Regions to check for snapshots
    Returns:
        dict: Mapping regions to corresponding list of snapshot_ids.
    """
    ec2_data = deepcopy(session.EC2)
    if 'Snapshots' not in ec2_data:
        fields = ['EC2', 'Snapshots']
        module = 'enum_ebs_volumes_snapshots'
        fetched_ec2_instances = pacu.fetch_data(fields, module)
        if fetched_ec2_instances is False:
            return None
    snapshot_ids = {}
    for region in regions:
        snapshot_ids[region] = []
    for snapshot in ec2_data['Snapshots']:
        if snapshot['Region'] in regions:
            snapshot_ids[snapshot['Region']].append(snapshot['SnapshotId'])
    return snapshot_ids


def get_volumes(pacu, session, regions):
    """Returns volumes given an AWS region
    Args:
        pacu (Main): Reference to Pacu
        session (PacuSession): Reference to the Pacu session database
        regions (list): Regions to check for volumes
    Returns:
        dict: Mapping regions to corresponding list of snapshot_ids.
    """
    ec2_data = deepcopy(session.EC2)
    if 'Snapshots' not in ec2_data:
        fields = ['EC2', 'Volumes']
        module = 'enum_ebs_volumes_snapshots'
        fetched_ec2_instances = pacu.fetch_data(fields, module)
        if fetched_ec2_instances is False:
            return None
    volume_ids = {}
    for region in regions:
        volume_ids[region] = {'available': [], 'in_use': []}
    for volume in ec2_data['Volumes']:
        if volume['Region'] in regions:
            if volume['State'] == 'available':
                volume_ids[volume['Region']]['available'].append(volume['VolumeId'])
            elif volume['State'] == 'in-use':
                volume_ids[volume['Region']]['in_use'].append(volume['VolumeId'])
    return volume_ids


def generate_volumes_from_snapshots(client, snapshots, zone):
    """ Returns a list of generated volumes"""
    volume_ids = []
    waiter = client.get_waiter('snapshot_completed')
    waiter.wait(SnapshotIds=snapshots)
    for snapshot in snapshots:
        response = client.create_volume(
            SnapshotId=snapshot, AvailabilityZone=zone)
        volume_ids.append(response['VolumeId'])
    return volume_ids


def generate_snapshots_from_volumes(client, volume_ids):
    """Returns a list of generated snapshots volumes"""
    snapshot_ids = []
    for volume in volume_ids:
        response = client.create_snapshot(VolumeId=volume)
        snapshot_ids.append(response['SnapshotId'])
    return snapshot_ids


def delete_volumes(client, volumes):
    """Deletes a given list of volumes"""
    failed_volumes = []
    for volume in volumes:
        try:
            client.delete_volume(VolumeId=volume)
        except ClientError:
            failed_volumes.append(volume)
    return failed_volumes


def delete_snapshots(client, snapshots):
    """Deletes a given list of snapshots"""
    failed_snapshots = []
    for snapshot in snapshots:
        try:
            client.delete_snapshot(SnapshotId=snapshot)
        except ClientError:
            failed_snapshots.append(snapshot)
    return failed_snapshots


def process_volumes(pacu, client, instance, zone, volumes):
    """Takes in a volume set, creates copies, and loads them onto an instance"""
    temp_snaps = generate_snapshots_from_volumes(client, volumes)
    temp_volumes = generate_volumes_from_snapshots(client, temp_snaps, zone)
    load_volumes(client, pacu.print, pacu.input, instance, temp_volumes)
    delete_volumes(client, temp_volumes)
    delete_snapshots(client, temp_snaps)
    return True


def main(args, pacu):
    """Main module function, called from Pacu"""
    args = parser.parse_args(args)
    session = pacu.get_active_session()
    print = pacu.print

    summary_data = {}

    regions = pacu.get_regions('ec2')
    snapshots = get_snapshots(pacu, session, regions)
    volumes = get_volumes(pacu, session, regions)
    for region in regions:
        client = pacu.get_boto3_client('ec2', region)
        if args.instance:
            instance = args.instance.split('@')[0]
            zone = args.instance.split('@')[1]
        else:
            instance, zone = get_interactive_instance(pacu, session, region)
        available_volumes = volumes[region]['available']
        in_use_volumes = volumes[region]['in_use']

        print('  Attaching initial volumes...')
        process_volumes(pacu, client, instance, zone, available_volumes)
        print('  Finished attaching initial volumes')

        print('  Attaching in-use volumes...')
        process_volumes(pacu, client, instance, zone, in_use_volumes)
        print('  Finished attaching in-use volumes')

        print('  Attaching volumes from existing snapshots')
        temp_volumes = generate_volumes_from_snapshots(
            client, snapshots[region], zone)
        load_volumes(client, print, pacu.input, instance, temp_volumes)
        delete_volumes(client, temp_volumes)
        print('  Finished attaching existing snapshot volumes ')

    return summary_data


def summary(data, pacu):
    """Returns a formatted string based on passed data."""
    return str(data)
