'''
Train self-supervised task (rotation prediction) task; current good dataset to use is xyz-axis_shuffled; 
Currently have a pre-trained model for this, which is referenced in semi_sup script
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

from autolab_core import YamlConfig, RigidTransform
from unsupervised_rbt import TensorDataset
from unsupervised_rbt.models import ResNetSiameseNetwork, InceptionSiameseNetwork
from perception import DepthImage, RgbdImage

def train(dataset, batch_size):
    '''Train model specified in main and return training loss and classification accuracy'''
    model.train()
    train_loss, correct, total = 0, 0, 0
    
    train_indices = dataset.split('train')[0][:10000]
    N_train = len(train_indices)
    n_train_steps = N_train//batch_size
    for step in tqdm(range(n_train_steps)):
        batch = dataset.get_item_list(train_indices[step*batch_size : (step+1)*batch_size])
        depth_image1 = (batch["depth_image1"] * 255).astype(int)
        depth_image2 = (batch["depth_image2"] * 255).astype(int)
        
        im1_batch = Variable(torch.from_numpy(depth_image1).float()).to(device)
        im2_batch = Variable(torch.from_numpy(depth_image2).float()).to(device)
        transform_batch = Variable(torch.from_numpy(batch["transform"].astype(int))).to(device)
    
#         print(depth_image1.shape)
#         print(depth_image2.shape)
        
#         if step > 20:
#             for i in range(batch_size):
#                 plt.subplot(121)
#                 depth_image_show1 = depth_image1[i][0]
#                 plt.imshow(depth_image_show1, cmap='gray')
#                 plt.subplot(122)
#                 depth_image_show2 = depth_image2[i][0]
#                 plt.imshow(depth_image_show2, cmap='gray')
#                 plt.title('Transform: {}'.format(transform_batch[i]))
#                 plt.show()

        optimizer.zero_grad()
        pred_transform = model(im1_batch, im2_batch)
#         print("TRANSFORM BATCH")
#         print(transform_batch)
        _, predicted = torch.max(pred_transform, 1)
#         print("PRED TRANSFORM")
#         print(predicted)
        correct += (predicted == transform_batch).sum().item()
        total += transform_batch.size(0)
        
        loss = criterion(pred_transform, transform_batch)
        loss.backward()
        train_loss += loss.item()
        optimizer.step()
        
    class_acc = 100 * correct/total
    return train_loss/n_train_steps, class_acc

def test(dataset, batch_size):
    """
    Return loss and classification accuracy of the model on the test data
    """
    model.eval()
    test_loss, correct, total = 0, 0, 0

    test_indices = dataset.split('train')[1][:1000]
    N_test = len(test_indices)
    n_test_steps = N_test // batch_size
    with torch.no_grad():
        for step in tqdm(range(n_test_steps)):
            batch = dataset.get_item_list(test_indices[step*batch_size : (step+1)*batch_size])
            depth_image1 = (batch["depth_image1"] * 255).astype(int)
            depth_image2 = (batch["depth_image2"] * 255).astype(int)
            im1_batch = Variable(torch.from_numpy(depth_image1).float()).to(device)
            im2_batch = Variable(torch.from_numpy(depth_image2).float()).to(device)
            transform_batch = Variable(torch.from_numpy(batch["transform"].astype(int))).to(device)
            pred_transform = model(im1_batch, im2_batch)
#             print("TRUE TRANSFORMS")
#             print(transform_batch)
            _, predicted = torch.max(pred_transform, 1)
#             print("PREDICTED TRANSFORMS")
#             print(predicted)
            correct += (predicted == transform_batch).sum().item()
            total += transform_batch.size(0)
            
            loss = criterion(pred_transform, transform_batch)
            test_loss += loss.item()
            
    class_acc = 100 * correct/total
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
                                           'cfg/tools/unsup_rbt_train.yaml')
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

        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        model = ResNetSiameseNetwork(config['pred_dim'], n_blocks=1, embed_dim=20).to(device)
#         model = InceptionSiameseNetwork(config['pred_dim']).to(device)
        criterion = nn.CrossEntropyLoss()
        optimizer = optim.Adam(model.parameters())
        
        if not os.path.exists(args.dataset + "/splits/train"):
            print("Created Train Split")
            dataset.make_split("train", train_pct=0.8)

        train_losses, test_losses, train_accs, test_accs = [], [], [], []
        for epoch in range(config['num_epochs']):
            train_loss, train_acc = train(dataset, config['batch_size'])
            test_loss, test_acc = test(dataset, config['batch_size'])
            train_losses.append(train_loss)
            test_losses.append(test_loss)
            train_accs.append(train_acc)
            test_accs.append(test_acc)
            print("Epoch %d, Train Loss = %f, Train Acc = %.2f %%, Test Loss = %f, Test Acc = %.2f %%" % (epoch, train_loss, train_acc, test_loss, test_acc))
            pickle.dump({"train_loss" : train_losses, "train_acc" : train_accs, "test_loss" : test_losses, "test_acc" : test_accs}, open( config['losses_f_name'], "wb"))
            torch.save(model.state_dict(), config['model_save_dir'])
            
    else:
        model = ResNetSiameseNetwork(config['pred_dim'])
        model.load_state_dict(torch.load(config['model_save_dir']))
        display_conv_layers(model)

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
        plt.savefig(config['losses_plot_f_name'])
        plt.close()
