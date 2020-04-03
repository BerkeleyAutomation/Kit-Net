'''
This script generates data for the self-supervised rotation prediction task
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
import pickle
import torch
import cv2
from unsupervised_rbt.models import ResNetSiameseNetwork, InceptionSiameseNetwork
from unsupervised_rbt.losses.shapematch import ShapeMatchLoss

def normalize(z):
    return z / np.linalg.norm(z)

def Generate_Quaternion():
    """Generate a random quaternion with conditions.
    To avoid double coverage and limit our rotation space, 
    we make sure the real component is positive and have 
    the greatest magnitude. Sample axes randomly. Sample degree uniformly
    """
    axis = np.random.normal(0, 1, 3)
    axis = axis / np.linalg.norm(axis) 
    angle = np.random.uniform(0,np.pi/6)
    quat = Rotation.from_rotvec(axis * angle).as_quat()
    if quat[3] < 0:
        quat = -1 * quat
    # print("Quaternion is ", 180/np.pi*np.linalg.norm(Rotation.from_quat(random_quat).as_rotvec()))
    return quat

def Generate_Quaternion_SO3():
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

def Rotation_to_Quaternion(rot_matrix):
    """Take in an object's 4x4 pose matrix and return a quaternion
    """
    quat = Rotation.from_dcm(rot_matrix[:3,:3]).as_quat()
    if quat[3] < 0:
        quat = -quat
    return quat

def Generate_Random_Transform(center_of_mass):
    """Create a matrix that will randomly rotate an object about an axis by a randomly sampled quaternion
    """
    quat = np.zeros(4)
    uniforms = np.random.uniform(0, 1, 3)
    one_minus_u1, u1 = np.sqrt(1 - uniforms[0]), np.sqrt(uniforms[0])
    uniforms_pi = 2*np.pi*uniforms
    quat = np.array(
        [one_minus_u1 * np.sin(uniforms_pi[1]),
        one_minus_u1 * np.cos(uniforms_pi[1]),
        u1 * np.sin(uniforms_pi[2]),
        u1 * np.cos(uniforms_pi[2])])

    quat = normalize(quat)
    return Quaternion_to_Rotation(quat, center_of_mass)

def Generate_Random_Z_Transform(center_of_mass):
    """Create a matrix that will randomly rotate an object about the z-axis by a random angle.
    """
    z_angle = 2*np.pi*np.random.random()
    return RigidTransform.rotation_from_axis_and_origin(axis=[0, 0, 1], origin=center_of_mass, angle=z_angle).matrix

def Plot_Image(image, filename):
    """x
    """
    cv2.imwrite(filename, image)
    fname2 = filename[:-4] + "_plt.png"
    fig1 = plt.imshow(image, cmap='gray')
    fig1.axes.get_xaxis().set_visible(False)
    fig1.axes.get_yaxis().set_visible(False)
    plt.show()
    plt.savefig(fname2)
    plt.close()

def Save_Poses(pose_matrix ,index):
    """x
    """
    np.savetxt(base_path + "/poses/matrix_" + index + ".txt", pose_matrix)
    pose_quat = Rotation_to_Quaternion(pose_matrix)
    np.savetxt(base_path + "/poses/quat_" + index + ".txt", pose_quat)

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
    table_mesh.visual.vertex_colors = [[0 for c in r] for r in table_mesh.visual.vertex_colors]
    table_mesh = Mesh.from_trimesh(table_mesh)
    table_node = Node(mesh=table_mesh, matrix=table_tf.matrix)
    scene.add_node(table_node)

    # scene.add(Mesh.from_trimesh(table_mesh), pose=table_tf.matrix, name='table')
    return scene, renderer

def parse_args():
    """Parse arguments from the command line.
    -config to input your own yaml config file. Default is data_gen_quat.yaml
    """
    parser = argparse.ArgumentParser()
    default_config_filename = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                                           '..',
                                           'cfg/tools/data_gen_quat.yaml')
    parser.add_argument('-config', type=str, default=default_config_filename)
    parser.add_argument('--start', action='store_true')
    args = parser.parse_args()
    return args

if __name__ == "__main__":
    args = parse_args()
    config = YamlConfig(args.config)

    # dataset configuration
    tensor_config = config['dataset']['tensors']

    scene, renderer = create_scene()
    dataset_name_list = ['3dnet', 'thingiverse', 'kit']
    mesh_dir = config['state_space']['heap']['objects']['mesh_dir']
    mesh_dir_list = [os.path.join(mesh_dir, dataset_name) for dataset_name in dataset_name_list]
    obj_config = config['state_space']['heap']['objects']
    mesh_lists = [os.listdir(mesh_dir) for mesh_dir in mesh_dir_list]

    obj_id = 0

    for mesh_dir, mesh_list in zip(mesh_dir_list, mesh_lists):
        for mesh_filename in mesh_list:
            obj_id += 1
            if obj_id != 4:
                continue
            # if obj_id > 20:
            #     break
            # sys.exit(0)

            print(colored('------------- Object ID ' + str(obj_id) + ' -------------', 'red'))

            # load object mesh
            mesh = trimesh.load_mesh(os.path.join(mesh_dir, mesh_filename))
            points = mesh.vertices

            obj_mesh = Mesh.from_trimesh(mesh)
            object_node = Node(mesh=obj_mesh, matrix=np.eye(4))
            scene.add_node(object_node)

            # light_pose = np.eye(4)
            # light_pose[:,3] = np.array([0.5,0.5,1,1])
            # scene.add(pyrender.PointLight(color=[1.0, 1.0, 1.0], intensity=2.0), pose=light_pose) # for rgb?

            # calculate stable poses
            stable_poses, _ = mesh.compute_stable_poses(
                sigma=obj_config['stp_com_sigma'],
                n_samples=obj_config['stp_num_samples'],
                threshold=obj_config['stp_min_prob']
            )

            if len(stable_poses) == 0:
                print("No Stable Poses")
                scene.remove_node(object_node)
                continue
            
            base_path = "controller/obj" + str(obj_id)
            # iterate over all stable poses of the object
            goal_pose_matrix = stable_poses[0]
            ctr_of_mass = goal_pose_matrix[0:3, 3]

            Save_Poses(goal_pose_matrix, "goal")

            # Render image 2, which will be the goal image of the object in a stable pose
            scene.set_pose(object_node, pose=goal_pose_matrix)
            image2 = renderer.render(scene, flags=RenderFlags.DEPTH_ONLY)
            image2 = (image2*255).astype(int)
            Plot_Image(image2, base_path + "/images/goal.png")

            # Render image 1, which will be 30 degrees away from the goal
            rot_quat = Generate_Quaternion()
            start_pose_matrix = Quaternion_to_Rotation(rot_quat, ctr_of_mass) @ goal_pose_matrix
            Save_Poses(start_pose_matrix, "0")

            scene.set_pose(object_node, pose=start_pose_matrix)
            image1 = renderer.render(scene, flags=RenderFlags.DEPTH_ONLY)
            image1 = (image1*255).astype(int)
            Plot_Image(image1, base_path + "/images/0.png")

            device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            model = ResNetSiameseNetwork(4, n_blocks=1, embed_dim=1024).to(device)
            model.load_state_dict(torch.load("models/564obj_1024.pt"))
            model.eval()

            I_s = cv2.imread(base_path +"/images/0.png", -1)
            print(I_s.shape)
            I_g = cv2.imread(base_path +"/images/goal.png", -1)
            im1_batch = torch.Tensor(torch.from_numpy(I_s).float()).to(device).unsqueeze(0).unsqueeze(0)
            im2_batch = torch.Tensor(torch.from_numpy(I_g).float()).to(device).unsqueeze(0).unsqueeze(0)
            print(im1_batch.size())
            cur_pose_matrix = np.loadtxt(base_path +"/poses/matrix_0.txt")
            for i in range(1,10):
                ctr_of_mass = cur_pose_matrix[:3,3]
                pred_quat = model(im1_batch,im2_batch).detach().cpu().numpy()[0]
                cur_pose_matrix = Quaternion_to_Rotation(pred_quat, ctr_of_mass) @ cur_pose_matrix
                Save_Poses(cur_pose_matrix, str(i))
                scene.set_pose(object_node, pose=cur_pose_matrix)
                cur_image = renderer.render(scene, flags=RenderFlags.DEPTH_ONLY)
                cur_image = (cur_image*255).astype(int)
                Plot_Image(cur_image, base_path + "/images/" + str(i) + ".png")
                im1_batch = torch.Tensor(torch.from_numpy(cur_image).float()).to(device).unsqueeze(0).unsqueeze(0)

            
            # delete the object to make room for the next
            scene.remove_node(object_node)
            losses = []
            quat_goal = np.loadtxt(base_path + "/poses/quat_goal.txt")
            print(quat_goal)
            for i in range(10):
                q = np.loadtxt(base_path + "/poses/quat_" + str(i) + ".txt")
                print(q)
                print(np.dot(q,quat_goal))
                losses.append(np.arccos(np.abs(np.dot(quat_goal, q)))*180/np.pi*2)
            print(np.round(losses,2))
            plt.plot(losses)
            plt.title("Angle Difference Between Iteration Orientation and Goal Orientation")
            plt.ylabel("Angle Difference (Degrees)")
            plt.xlabel("Iteration Number")
            plt.savefig(base_path + "/images/loss.png")

    # pickle.dump(all_points, open("cfg/tools/point_clouds", "wb"))
    # pickle.dump(all_points300, open("cfg/tools/point_clouds300", "wb"))
    # pickle.dump(all_scales, open("cfg/tools/scales", "wb"))
