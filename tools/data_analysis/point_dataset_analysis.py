'''
This script analyses the dataset for the self-supervised rotation prediction task.
Symmetric objects: 19, 283
Good objects: 4,5,327
'''

from autolab_core import YamlConfig, RigidTransform, TensorDataset
from scipy.spatial.transform import Rotation
import os

# Use this if you are SSH
os.environ["PYOPENGL_PLATFORM"] = 'egl'
# os.environ["PYOPENGL_PLATFORM"] = 'osmesa'

import numpy as np
import trimesh
import itertools
import sys
import argparse
import pyrender
from pyrender import (Scene, IntrinsicsCamera, Mesh,
                      Viewer, OffscreenRenderer, RenderFlags, Node)
from sd_maskrcnn.envs import CameraStateSpace

import matplotlib.pyplot as plt
import random
from termcolor import colored

from skimage.io import imread, imshow
from skimage.transform import resize
from skimage.feature import hog
from skimage import exposure
import pickle
import torch

from unsupervised_rbt.losses.shapematch import ShapeMatchLoss

def normalize(z):
    return z / np.linalg.norm(z)

def Generate_Quaternion():
    """Generate a random quaternion with conditions.
    To avoid double coverage and limit our rotation space, 
    we make sure the real component is positive and have 
    the greatest magnitude. We also limit rotations to less
    than 60 degrees. We sample according to the following links:
    https://www.euclideanspace.com/maths/geometry/rotations/conversions/quaternionToAngle/index.htm
    http://planning.cs.uiuc.edu/node198.html
    """
    quat = np.zeros(4)
    # while np.max(np.abs(quat)) < 0.866: # 60 degrees
    # while np.max(np.abs(quat)) < 0.92388: # 45 degrees
    while np.max(np.abs(quat)) < 0.96592:  # 30 degrees
      uniforms = np.random.uniform(0, 1, 3)
      one_minus_u1, u1 = np.sqrt(1 - uniforms[0]), np.sqrt(uniforms[0])
      uniforms_pi = 2*np.pi*uniforms
      quat = np.array(
          [one_minus_u1 * np.sin(uniforms_pi[1]),
           one_minus_u1 * np.cos(uniforms_pi[1]),
           u1 * np.sin(uniforms_pi[2]),
           u1 * np.cos(uniforms_pi[2])])

    max_i = np.argmax(np.abs(quat))
    quat[3], quat[max_i] = quat[max_i], quat[3]
    if quat[3] < 0:
        quat = -1 * quat
    # print("Quaternion is ", 180/np.pi*np.linalg.norm(Rotation.from_quat(random_quat).as_rotvec()))
    return quat


def Generate_Quaternion_i():
    """Generate a random quaternion with conditions.
    To avoid double coverage and limit our rotation space, 
    we make sure the i component is positive and
    has the greatest magnitude.
    """
    quat = np.random.uniform(-1, 1, 4)
    quat = normalize(quat)
    max_i = np.argmax(np.abs(quat))
    quat[0], quat[max_i] = quat[max_i], quat[0]
    if quat[0] < 0:
        quat = -1 * quat
    return quat


def Quaternion_String(quat):
    """Converts a 4 element quaternion to a string for printing
    """
    quat = np.round(quat, 3)
    return str(quat[3]) + " + " + str(quat[0]) + "i + " + str(quat[1]) + "j + " + str(quat[2]) + "k"


def Quaternion_to_Rotation(quaternion, center_of_mass):
    """Take in an object's center of mass and a quaternion, and
    return a rotation matrix.
    """
    rotation_vector = Rotation.from_quat(quaternion).as_rotvec()
    angle = np.linalg.norm(rotation_vector)
    axis = rotation_vector / angle
    return RigidTransform.rotation_from_axis_and_origin(axis=axis, origin=center_of_mass, angle=angle).matrix


def Generate_Random_Transform(center_of_mass):
    """Create a matrix that will randomly rotate an object about an axis by a random angle between 0 and 45.
    """
    angle = 0.25*np.pi*np.random.random()
    # print(angle * 180 / np.pi)
    axis = np.random.rand(3)
    axis = axis / np.linalg.norm(axis)
    return RigidTransform.rotation_from_axis_and_origin(axis=axis, origin=center_of_mass, angle=angle).matrix


def Generate_Random_Z_Transform(center_of_mass):
    """Create a matrix that will randomly rotate an object about the z-axis by a random angle.
    """
    z_angle = 2*np.pi*np.random.random()
    return RigidTransform.rotation_from_axis_and_origin(axis=[0, 0, 1], origin=center_of_mass, angle=z_angle).matrix


def Plot_Datapoint(datapoint):
    """Takes in a datapoint of our Tensor Dataset, and plots its two images for visualizing their 
    iniitial pose and rotation.
    """
    plt.figure(figsize=(14, 7))
    plt.subplot(121)
    fig1 = plt.imshow(datapoint["depth_image1"][:, :, 0], cmap='gray')
    plt.title('Stable pose')
    plt.subplot(122)
    fig2 = plt.imshow(datapoint["depth_image2"][:, :, 0], cmap='gray')
    fig1.axes.get_xaxis().set_visible(False)
    fig1.axes.get_yaxis().set_visible(False)
    fig2.axes.get_xaxis().set_visible(False)
    fig2.axes.get_yaxis().set_visible(False)
    plt.title('After Rigid Transformation: ' + Quaternion_String(datapoint["quaternion"]))
    plt.show()
    # plt.savefig("pictures/allobj/obj" + str(datapoint['obj_id']) + ".png")
    # plt.close()


def addNoise(image, std=0.001):
    """Adds noise to image array.
    """
    noise = np.random.normal(0, std, image.shape)
    return image + noise


def create_scene(data_gen=True):
    """Create scene for taking depth images.
    """
    scene = Scene(ambient_light=[0.02, 0.02, 0.02], bg_color=[1.0, 1.0, 1.0])
    renderer = OffscreenRenderer(viewport_width=1, viewport_height=1)
    if not data_gen:
        config2 = YamlConfig(os.path.join(os.path.dirname(os.path.realpath(__file__)), '..',
                                          'cfg/tools/data_gen_quat.yaml'))
    else:
        config2 = config
    # initialize camera and renderer
    cam = CameraStateSpace(config2['state_space']['camera']).sample()

    # If using older version of sd-maskrcnn
    # camera = PerspectiveCamera(cam.yfov, znear=0.05, zfar=3.0,
    #                                aspectRatio=cam.aspect_ratio)
    camera = IntrinsicsCamera(cam.intrinsics.fx, cam.intrinsics.fy,
                              cam.intrinsics.cx, cam.intrinsics.cy)
    renderer.viewport_width = cam.width
    renderer.viewport_height = cam.height

    pose_m = cam.pose.matrix.copy()
    pose_m[:, 1:3] *= -1.0
    scene.add(camera, pose=pose_m, name=cam.frame)
    scene.main_camera_node = next(iter(scene.get_nodes(name=cam.frame)))

    # Add Table
    table_mesh = trimesh.load_mesh(
        os.path.join(
            os.path.dirname(os.path.realpath(__file__)),
            '../data/objects/plane/plane.obj',
        )
    )
    table_tf = RigidTransform.load(
        os.path.join(
            os.path.dirname(os.path.realpath(__file__)),
            '../data/objects/plane/pose.tf',
        )
    )
    table_mesh = Mesh.from_trimesh(table_mesh)
    table_node = Node(mesh=table_mesh, matrix=table_tf.matrix)
    scene.add_node(table_node)

    # scene.add(Mesh.from_trimesh(table_mesh), pose=table_tf.matrix, name='table')
    return scene, renderer


def parse_args():
    """Parse arguments from the command line.
    -config to input your own yaml config file. Default is data_gen_quat.yaml
    -dataset to input a name for your dataset. Should start with quaternion
    --objpred to use the num_samples_per_obj_objpred option of your config
    """
    parser = argparse.ArgumentParser()
    default_config_filename = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                                           '..',
                                           'cfg/tools/data_gen_quat.yaml')
    parser.add_argument('-config', type=str, default=default_config_filename)
    parser.add_argument('-dataset', type=str, required=True)
    parser.add_argument('--objpred', action='store_true')
    args = parser.parse_args()
    return args


if __name__ == "__main__":
    args = parse_args()
    config = YamlConfig(args.config)
    # to adjust
    name_gen_dataset = args.dataset
    if config['debug']:
        name_gen_dataset += "_junk"

    # dataset configuration
    tensor_config = config['dataset']['tensors']
    dataset = TensorDataset("/nfs/diskstation/projects/unsupervised_rbt/" + name_gen_dataset + "/", tensor_config)
    datapoint = dataset.datapoint_template

    scene, renderer = create_scene()
    dataset_name_list = ['3dnet', 'thingiverse', 'kit']
    mesh_dir = config['state_space']['heap']['objects']['mesh_dir']
    mesh_dir_list = [os.path.join(mesh_dir, dataset_name) for dataset_name in dataset_name_list]
    obj_config = config['state_space']['heap']['objects']
    mesh_lists = [os.listdir(mesh_dir) for mesh_dir in mesh_dir_list]
    print("NUM OBJECTS")
    print([len(a) for a in mesh_lists])

    if args.objpred:
        num_samples_per_obj = config['num_samples_per_obj_objpred']
    else:
        num_samples_per_obj = config['num_samples_per_obj']

    obj_id = 0
    data_point_counter = 0
    objects_added = {}
    scores_symmetry, rot_similarity, scores_features = [], {}, {}
    for mesh_dir, mesh_list in zip(mesh_dir_list, mesh_lists):
        for mesh_filename in mesh_list:
            obj_id += 1
            # dataset.flush()
            # sys.exit(0)

            print(colored('------------- Object ID ' + str(obj_id) + ' -------------', 'red'))

            # load object mesh
            mesh = trimesh.load_mesh(os.path.join(mesh_dir, mesh_filename))
            obj_mesh = Mesh.from_trimesh(mesh)
            object_node = Node(mesh=obj_mesh, matrix=np.eye(4))
            scene.add_node(object_node)
            # scene.add(pyrender.PointLight(color=[1.0, 1.0, 1.0], intensity=2.0), pose=np.eye(4)) # for rgb?
            scores_features[obj_id], rot_similarity[obj_id] = [], []
            obj_symmetry = mesh.symmetry
            # print("Object ID", obj_id, "has symmetry", obj_symmetry)
            if obj_symmetry or obj_id != 283:
                scores_symmetry.append(-np.infty)
                scene.remove_node(object_node)
                continue
            else:
                scores_symmetry.append(0)
                scores_features[obj_id] = len(mesh.vertices)
                # print("no symmetry")

            points = mesh.vertices / mesh.scale * 10
            # calculate stable poses
            stable_poses, _ = mesh.compute_stable_poses(
                sigma=obj_config['stp_com_sigma'],
                n_samples=obj_config['stp_num_samples'],
                threshold=0.01
            )

            if len(stable_poses) == 0:
                scene.remove_node(object_node)
                continue
            iteration = 0
            # iterate over all stable poses of the object
            for j, pose_matrix in enumerate(stable_poses):
                # print("Stable Pose number:", j)
                ctr_of_mass = pose_matrix[0:3, 3]
                # Render image 1, which will be our original image at the stable pose
                scene.set_pose(object_node, pose=pose_matrix)
                image1 = 1 - renderer.render(scene, flags=RenderFlags.DEPTH_ONLY)

                points1 = pose_matrix[:3,:3] @ points.T
                # print(points.shape, points1.shape)
                axes, angles = [[1,0,0],[0,1,0], [0,0,1]], [60/180*np.pi,np.pi/2,120/ 180*np.pi,np.pi]
                for axis in axes:
                    for angle in angles:
                        rot_matrix = RigidTransform.rotation_from_axis_and_origin(axis=axis, origin=ctr_of_mass, angle=angle).matrix
                        # Render image 2, which will be image 1 rotated according to our specification
                        new_pose = rot_matrix @ pose_matrix
                        scene.set_pose(object_node, pose=new_pose)
                        image2 = 1 - renderer.render(scene, flags=RenderFlags.DEPTH_ONLY)
                        mse = np.linalg.norm(image1 - image2) #* black_pixels / 128 / 128

                        points2 = rot_matrix[:3,:3] @ points1
                        points1_tensor, points2_tensor = torch.Tensor([points1.T]), torch.Tensor([points2.T])
                        mse = ShapeMatchLoss.PointCloudDistance(points1_tensor,points2_tensor)
                        rot_similarity[obj_id].append(mse)
                        if mse < 0.17:
                            print(mse)
                            # print("Too similar MSE:", mse)
                            if config['debug'] or angle == 120 / 180 * np.pi :
                                print(np.linalg.norm(image1 - image2))
                                plt.figure(figsize=(14, 7))
                                plt.subplot(121)
                                fig1 = plt.imshow(image1, cmap='gray')
                                plt.title('Stable pose')
                                plt.subplot(122)
                                fig2 = plt.imshow(image2, cmap='gray')
                                fig1.axes.get_xaxis().set_visible(False)
                                fig1.axes.get_yaxis().set_visible(False)
                                fig2.axes.get_xaxis().set_visible(False)
                                fig2.axes.get_yaxis().set_visible(False)
                                plt.title('After Rotation around: ' + str(axis) + " w angle " + str(angle*180//np.pi))
                                plt.savefig("unsupervised_rbt/losses/debug" + str(iteration) + ".png")
                                iteration += 1
                                # plt.show()

            # delete the object to make room for the next
            scene.remove_node(object_node)
            if np.min(rot_similarity[obj_id]) < 0.1:
                print("symmetric? ", np.min(rot_similarity[obj_id]))
            else:
                print("no symmetry")

    # np.savetxt("cfg/tools/data/scores_symmetry", np.array(scores_symmetry))
    # pickle.dump(rot_similarity, open("cfg/tools/data/rot_similarity", "wb"))
    # pickle.dump(scores_features, open("cfg/tools/data/scores_features", "wb"))

    dataset.flush()
