'''
Train supervised object matching task; use dataset downstream for now. This script creates
pairs that are half the same object paired with itself (w/random rigid body transform) and half 
object paired with different object and then trains a CNN to determine whether the objects
are the same object or not.
'''

import numpy as np
import argparse
import os
import matplotlib.pyplot as plt
import itertools
import torch
import torch.nn as nn
import torchvision
import torch.optim as optim
from torch.autograd import Variable
import pickle
import matplotlib.pyplot as plt
from tqdm import tqdm
from random import shuffle

from autolab_core import YamlConfig, RigidTransform
from unsupervised_rbt import TensorDataset
from unsupervised_rbt.models import ResNetSiameseNetwork
from perception import DepthImage, RgbdImage

def generate_data(dataset, num_pairs = 10000):
    """
    Generates a pair of depth images. Half will be from the same object, half will be from different objects.
    Chooses a random orientation from 20 different orientations. Binary Labels for if same object or not.
    """
    im1s, im2s, labels = [], [], []
    for _ in range(num_pairs):
        dp1_idx = np.random.randint(dataset.num_datapoints)
        dp2_idx, label = dp1_idx, 1 # same object
        
        im1_idx = np.random.randint(20)
        im2_idx = np.random.randint(20)
        
        im1s.append(255 * dataset[dp1_idx]['depth_images'][im1_idx])

        if np.random.random() < 0.5: # Makes half of the training data to be different objects
            while dp2_idx == dp1_idx:
                dp2_idx = np.random.randint(dataset.num_datapoints)
            label = 0

        im2s.append(255 * dataset[dp2_idx]['depth_images'][im2_idx])
        labels.append(label)
    im1s, im2s, labels = np.array(im1s), np.array(im2s), np.array(labels)
    return np.expand_dims(im1s, 1), np.expand_dims(im2s, 1), labels

def train(im1s, im2s, labels, batch_size):
    """
    Train the model specified in main, then return the loss and classification accuracy on
    80% of the training data. Uses tqdm to visualize progress.
    """
    model.train() 
    train_loss, correct, total = 0, 0, 0
    
    N_train = int(0.8 * im1s.shape[0])
    n_train_steps = N_train//batch_size
    for step in tqdm(range(n_train_steps)):
        im1_batch   = Variable(torch.from_numpy(im1s[step*batch_size : (step+1)*batch_size]).float()).to(device)
        im2_batch   = Variable(torch.from_numpy(im2s[step*batch_size : (step+1)*batch_size]).float()).to(device)
        label_batch = Variable(torch.from_numpy(labels[step*batch_size : (step+1)*batch_size]).float()).to(device)

        # for i in range(batch_size):
            # title = 'Same Object' if labels[step*batch_size + i] else 'Different Object'
            # plt.title(title)
            # plt.subplot(121)
            # depth_image_show1 = im1s[step*batch_size + i][0]
            # plt.axis('off')
            # plt.imshow(depth_image_show1, cmap='gray')
            # plt.subplot(122)
            # depth_image_show2 = im2s[step*batch_size + i][0]
            # plt.axis('off')
            # plt.imshow(depth_image_show2, cmap='gray')
            # plt.show()
       
        optimizer.zero_grad()
        prob = model(im1_batch, im2_batch)
        loss = criterion(prob, label_batch.long())
        _, predicted = torch.max(prob, 1)
#         output1, output2 = model(im1_batch, im2_batch)
#         loss = criterion(output1, output2, label_batch)
        
#         predicted = (prob > 0.5).float().flatten()
#         correct += (predicted == label_batch).sum().item()
#         total += label_batch.size(0)

        correct += (predicted == label_batch.long()).sum().item()
        total += label_batch.size(0)
        
        loss.backward()
        train_loss += loss.item()
        optimizer.step()
        
    class_acc = 100 * correct/total
    return train_loss/n_train_steps, class_acc

def test(im1s, im2s, labels, batch_size):
    model.eval()
    test_loss, correct, total = 0, 0, 0

    N_test = int(0.2 * im1s.shape[0])
    N_train = int(0.8 * im1s.shape[0])
    n_test_steps = N_test // batch_size
    im1s, im2s, labels = im1s[N_train:], im2s[N_train:], labels[N_train:]
    with torch.no_grad():
        for step in tqdm(range(n_test_steps)):
            im1_batch   = Variable(torch.from_numpy(im1s[step*batch_size   : (step+1)*batch_size]).float()).to(device)
            im2_batch   = Variable(torch.from_numpy(im2s[step*batch_size   : (step+1)*batch_size]).float()).to(device)
            label_batch = Variable(torch.from_numpy(labels[step*batch_size : (step+1)*batch_size]).float()).to(device)
       
            optimizer.zero_grad()
            prob = model(im1_batch, im2_batch)
            loss = criterion(prob, label_batch.long())
            _, predicted = torch.max(prob, 1)
#             output1, output2 = model(im1_batch, im2_batch)
#             loss = criterion(output1, output2, label_batch)
#             print("LABELS")
#             print(label_batch)
#             print("PREDICTED")
#             print(prob)
#             predicted = (prob > 0.5).float().flatten()
#             correct += (predicted == label_batch).sum().item()
#             total += label_batch.size(0)
            correct += (predicted == label_batch.long()).sum().item()
            total += label_batch.size(0)
            
            test_loss += loss.item()
            
    class_acc = 100 * correct/total
#     class_acc = 0
    return test_loss/n_test_steps, class_acc

def display_conv_layers(model):
    def imshow(img):
        img = img / 2 + 0.5     # unnormalize
        npimg = img.cpu().numpy()
        plt.imshow(np.transpose(npimg, (1, 2, 0)))
        plt.show()
    with torch.no_grad():
        imshow(torchvision.utils.make_grid(model.resnet.conv1.weight))

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--test', action='store_true')
    default_config_filename = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                                           '..',
                                           'cfg/tools/sup_obj_matching.yaml')
    parser.add_argument('-config', type=str, default=default_config_filename)
    parser.add_argument('-dataset', type=str, required=True)
    args = parser.parse_args()
    args.dataset = os.path.join('/nfs/diskstation/projects/unsupervised_rbt', args.dataset)
    return args

if __name__ == '__main__':
    args = parse_args()
    config = YamlConfig(args.config)

    if not args.test:
        dataset = TensorDataset.open(args.dataset)
        im1s, im2s, labels = generate_data(dataset)
        
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        model = ResNetSiameseNetwork(config['pred_dim']).to(device)
        criterion = nn.CrossEntropyLoss()
        optimizer = optim.Adam(model.parameters())
        
        if not os.path.exists(args.dataset + "/splits/train"):
            print("Created Train Split")
            dataset.make_split("train", train_pct=0.8)

        train_losses, test_losses, train_accs, test_accs = [], [], [], []
        for epoch in range(config['num_epochs']):
            train_loss, train_acc = train(im1s, im2s, labels, config['batch_size'])
            test_loss, test_acc = test(im1s, im2s, labels, config['batch_size'])
            train_losses.append(train_loss)
            test_losses.append(test_loss)
            train_accs.append(train_acc)
            test_accs.append(test_acc)
            print("Epoch %d, Train Loss = %f, Train Acc = %.2f %%, Test Loss = %f, Test Acc = %.2f %%" % (epoch, train_loss, train_acc, test_loss, test_acc))
            pickle.dump({"train_loss" : train_losses, "train_acc" : train_accs, "test_loss" : test_losses, "test_acc" : test_accs}, open( config['losses_f_name'], "wb"))
            torch.save(model.state_dict(), config['model_save_dir'])
            
    else:
#         model = ResNetDownstreamSiameseNetwork(config['pred_dim'])

        losses = pickle.load( open( config['losses_f_name'], "rb" ) )
        train_returns = np.array(losses["train_loss"])
        test_returns = np.array(losses["test_loss"])
        train_accs = np.array(losses["train_acc"])
        test_accs = np.array(losses["test_acc"])
        
        plt.plot(np.arange(len(train_returns)) + 1, train_returns, label="Training Loss")
        plt.plot(np.arange(len(test_returns)) + 1, test_returns, label="Testing Loss")
        plt.xlabel("Training Iteration")
        plt.ylabel("Loss")
        plt.title("Training Curve")
        plt.legend(loc='best')
        plt.savefig(config['losses_plot_f_name'])
        plt.close()
        
        plt.plot(np.arange(len(train_accs)) + 1, train_accs, label="Training Acc")
        plt.plot(np.arange(len(test_accs)) + 1, test_accs, label="Testing Acc")
        plt.xlabel("Training Iteration")
        plt.ylabel("Classification Accuracy")
        plt.title("Training Curve")
        plt.legend(loc='best')
        plt.savefig(config['accs_plot_f_name'])
        plt.close()
