import numpy as np
import argparse
import os
import matplotlib.pyplot as plt
import matplotlib.patheffects as PathEffects
import itertools
import torch
from torch.autograd import Variable
import torch.nn as nn
import torch.optim as optim
import pickle
import matplotlib.pyplot as plt
from tqdm import tqdm
from sklearn.manifold import TSNE
import seaborn as sns
from random import shuffle

from autolab_core import YamlConfig, RigidTransform
from unsupervised_rbt import TensorDataset
from unsupervised_rbt.models import LinearEmbeddingClassifier
from unsupervised_rbt.models import ResNetSiameseNetwork
from perception import DepthImage, RgbdImage

def train(dataset, batch_size):
    model.train()
    train_loss, correct, total = 0, 0, 0
    
    train_indices = dataset.split('train')[0][:10000]
    N_train = len(train_indices)
    # shuffle(train_indices)
    # N_train = int(0.8 * dataset.num_datapoints)
    # N_train = 10000
    n_train_steps = N_train//batch_size
    for step in tqdm(range(n_train_steps)):
        batch = dataset.get_item_list(train_indices[step*batch_size: (step+1)*batch_size])
        depth_image1 = (batch["depth_image1"] * 255).astype(int)
        depth_image2 = (batch["depth_image2"] * 255).astype(int)
        
        im1_batch = Variable(torch.from_numpy(depth_image1).float()).to(device)
        im2_batch = Variable(torch.from_numpy(depth_image2).float()).to(device)
        label_batch = Variable(torch.from_numpy(batch["obj_id"].astype(int))).to(device)

        optimizer.zero_grad()
        output1 = model(im1_batch)
        output2 = model(im2_batch)
        # print(output1)
        # print(label_batch)
        # assert False
        loss = criterion(output1, label_batch.long()) + criterion(output2, label_batch.long())
        _, predicted1 = torch.max(output1, 1)
        _, predicted2 = torch.max(output2, 1)
        
        correct += (predicted1 == label_batch.long()).sum().item()
        correct += (predicted2 == label_batch.long()).sum().item()
        total += label_batch.size(0)
        total += label_batch.size(0)
        
        loss.backward()
        train_loss += loss.item()
        optimizer.step()
        
    class_acc = 100 * correct/total
    return train_loss/n_train_steps, class_acc

def test(dataset, batch_size):
    model.eval()
    test_loss, correct, total = 0, 0, 0

    test_indices = dataset.split('train')[1][:2000]
    # test_indices = dataset.split('train')[1]
    n_test = len(test_indices)
    n_test_steps = n_test // batch_size

    with torch.no_grad():
        for step in tqdm(range(n_test_steps)):
            batch = dataset.get_item_list(test_indices[step*batch_size: (step+1)*batch_size])
            depth_image1 = (batch["depth_image1"] * 255).astype(int)
            depth_image2 = (batch["depth_image2"] * 255).astype(int)
            
            im1_batch = Variable(torch.from_numpy(depth_image1).float()).to(device)
            im2_batch = Variable(torch.from_numpy(depth_image2).float()).to(device)
            label_batch = Variable(torch.from_numpy(batch["obj_id"].astype(int))).to(device)

            optimizer.zero_grad()
            output1 = model(im1_batch)
            output2 = model(im2_batch)
            loss = criterion(output1, label_batch.long()) + criterion(output2, label_batch.long())
            _, predicted1 = torch.max(output1, 1)
            _, predicted2 = torch.max(output2, 1)

            correct += (predicted1 == label_batch.long()).sum().item()
            correct += (predicted2 == label_batch.long()).sum().item()
            total += label_batch.size(0)
            total += label_batch.size(0)
            test_loss += loss.item()
            
    class_acc = 100 * correct/total
    return test_loss/n_test_steps, class_acc

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--test', action='store_true')
    default_config_filename = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                                           '..',
                                           'cfg/tools/semisup_obj_prediction_quat.yaml')
    parser.add_argument('-config', type=str, default=default_config_filename)
    parser.add_argument('-dataset', type=str, required=True)
    args = parser.parse_args()
    # args.dataset = os.path.join('/raid/mariuswiggert', args.dataset)
    args.dataset = os.path.join('/nfs/diskstation/projects/unsupervised_rbt', args.dataset)
    return args

if __name__ == '__main__':
    args = parse_args()
    config = YamlConfig(args.config)

    if not args.test:
        dataset = TensorDataset.open(args.dataset)
        
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        use_dropout = False
        model = LinearEmbeddingClassifier(config, config['pred_dim'],embed_dim=config['embed_dim'],dropout=False, init=False).to(device)
        # model = ResNetSiameseNetwork(config['pred_dim'], n_blocks=1, embed_dim=config['embed_dim'], norm=False).to(device)

        criterion = nn.CrossEntropyLoss()
        # params = list(model.fc_1.parameters()) + list(model.fc_2.parameters()) + list(model.final_fc.parameters())
        # optimizer = optim.Adam(params) # only train fc layer
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
