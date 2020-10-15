import torch
import torch.nn as nn
import torch.nn.functional as F

class ResNetSiameseNetwork(nn.Module):
    def __init__(self, transform_pred_dim, num_blocks = 1, embed_dim=1024, dropout=4, norm=True, image_dim = 128):
        super(ResNetSiameseNetwork, self).__init__()
        self.resnet = ResNet(BasicBlock, num_blocks, embed_dim, image_dim=image_dim)   # [1,1,1,1]
        self.fc_1 = nn.Linear(embed_dim*2, 1000) # was 200 before (but 50 achieves same result)
        self.fc_2 = nn.Linear(1000, 1000) #changed all from 1000
        # self.fc_3 = nn.Linear(1000, 1000) 
        self.final_fc = nn.Linear(1000, transform_pred_dim)
        self.dropout = nn.Dropout(dropout / 10) #0.4 for most
        self.norm = norm
        self.bn1 = nn.BatchNorm1d(1000)
        self.bn2 = nn.BatchNorm1d(1000)

    def forward(self, input1, input2):
        output1 = self.resnet(input1)
        output2 = self.resnet(input2)
        output_concat = torch.cat((output1, output2), 1)
        output = self.dropout(F.leaky_relu(self.fc_1(output_concat), 0.02))
        output = self.dropout(F.leaky_relu(self.fc_2(output), 0.02))
        # output = F.leaky_relu(self.bn1(self.fc_1(output_concat)), 0.02)
        # output = F.leaky_relu(self.bn2(self.fc_2(output)), 0.02)
                
        output = self.final_fc(output)
        # print(output)
        if self.norm:
            output = F.normalize(output)
        return output #Normalize for Quaternion Regression

class LinearEmbeddingClassifier(nn.Module):
    def __init__(self, config, num_classes, embed_dim=200, dropout=False, init=False):
        super(LinearEmbeddingClassifier, self).__init__()
#         print(init)
        siamese = ResNetSiameseNetwork(config['pred_dim'], n_blocks=1, embed_dim=embed_dim, dropout=dropout, norm=False)
        if init:
            print('------------- Loaded self-supervised model -------------')
            # siamese.load_state_dict(torch.load(config['unsup_model_save_dir']))
            new_state_dict = siamese.state_dict()
            load_params = torch.load(config['unsup_model_path'])
            load_params_new = load_params.copy()
            for layer_name in load_params:
                if not layer_name.startswith(('resnet')):
                    # print("deleting layer", layer_name)
                    del load_params_new[layer_name]
            new_state_dict.update(load_params_new)
            siamese.load_state_dict(new_state_dict)

        self.resnet = siamese.resnet
        self.fc_1 = nn.Linear(embed_dim, 1000) # was 200 before (but 50 achieves same result for rotation prediction)
        self.fc_2 = nn.Linear(1000, 1000)
        self.final_fc = nn.Linear(1000, num_classes)
        self.dropout = nn.Dropout(0.2)

    def forward(self, input1):
        output = self.resnet(input1)
        output = self.dropout(output)
        output = self.dropout(F.relu(self.fc_1(output)))
        output = self.dropout(F.relu(self.fc_2(output)))
        return self.final_fc(output)
    
class ResNetObjIdPred(nn.Module):
    def __init__(self, transform_pred_dim, dropout=False, embed_dim=200, n_blocks = 4):
        super(ResNetObjIdPred, self).__init__()
        blocks = [item for item in [1] for i in range(n_blocks)]
        self.resnet = ResNet(BasicBlock, blocks, embed_dim, dropout=False)   # [1,1,1,1]
        self.fc_1 = nn.Linear(embed_dim, 1000) # was 200 before (but 50 achieves same result)
        self.fc_2 = nn.Linear(1000, 1000)
        self.final_fc = nn.Linear(1000, transform_pred_dim)  
        self.dropout = nn.Dropout(0.2)

    def forward(self, input1):
        output = self.resnet(input1)
        output = self.dropout(F.relu(self.fc_1(output)))
        output = self.dropout(F.relu(self.fc_2(output)))
        return self.final_fc(output)

class BasicBlock(nn.Module):
    expansion = 1

    def __init__(self, in_planes, planes, strides=[1,1]):
        super(BasicBlock, self).__init__()
        self.conv1 = nn.Conv2d(in_planes, planes, kernel_size=3, stride=strides[0], padding=1, bias=False)
        self.bn1 = nn.BatchNorm2d(planes)
        self.conv2 = nn.Conv2d(planes, planes, kernel_size=3, stride=strides[1], padding=1, bias=False)
        self.bn2 = nn.BatchNorm2d(planes)

        self.shortcut = nn.Sequential()
        if strides[0] != 1 or in_planes != self.expansion*planes:
            self.shortcut = nn.Sequential(
                nn.Conv2d(in_planes, self.expansion*planes, kernel_size=1, stride=strides[0]*strides[1], bias=False),
                nn.BatchNorm2d(self.expansion*planes)
            )

    def forward(self, x):
        out = F.relu(self.bn1(self.conv1(x)))
        out = self.bn2(self.conv2(out))
        out += self.shortcut(x)
        out = F.relu(out)
        return out

class ResNet(nn.Module):
    def __init__(self, block, num_blocks, num_output=200, image_dim = 128):
        super(ResNet, self).__init__()
        self.in_planes = 64
        self.n_blocks = num_blocks
        
        self.conv1 = nn.Conv2d(1, 64, kernel_size=3, stride=2, padding=1, bias=False)
        self.bn1 = nn.BatchNorm2d(64)
        self.maxpool = nn.MaxPool2d(kernel_size=3, stride=2, padding=1)

        if image_dim == 128:
            if self.n_blocks == 1:
                self.layer1 = self._make_layer(block, 64, strides=[2,1])
                self.linear = nn.Linear(1024, num_output)
            elif self.n_blocks == 2:
                self.layer1 = self._make_layer(block, 64, strides=[1,1])
                self.layer2 = self._make_layer(block, 64, strides=[2,1])
                self.linear = nn.Linear(1024, num_output)
            elif self.n_blocks == 3:
                self.layer1 = self._make_layer(block, 64, strides=[1,1])
                self.layer2 = self._make_layer(block, 64, strides=[2,1])
                self.layer3 = self._make_layer(block, 64, strides=[2,1])
                self.linear = nn.Linear(1024, num_output)
            else:
                print("Error: number of blocks not in ResNet specification")
                assert False
        elif image_dim == 256:
            if self.n_blocks == 1:
                self.layer1 = self._make_layer(block, 64, strides=[2,2])
                self.linear = nn.Linear(4096, num_output)
            elif self.n_blocks == 2:
                self.layer1 = self._make_layer(block, 64, strides=[2,1])
                self.layer2 = self._make_layer(block, 64, strides=[2,1])
                self.linear = nn.Linear(4096, num_output)
            elif self.n_blocks == 3:
                self.layer1 = self._make_layer(block, 64, strides=[1,1])
                self.layer2 = self._make_layer(block, 64, strides=[2,1])
                self.layer3 = self._make_layer(block, 64, strides=[2,1])
                self.linear = nn.Linear(4096, num_output)
            else:
                print("Error: number of blocks not in ResNet specification")
                assert False

    def _make_layer(self, block, planes, strides):
        return nn.Sequential(block(self.in_planes, planes, strides))

    def forward_no_linear(self, x):
        out = F.relu(self.bn1(self.conv1(x)))
        out = self.maxpool(out)
        out = self.layer1(out)
        if self.n_blocks == 1:
            return out
        elif self.n_blocks == 2:  
            out = self.layer2(out)
            return out
        elif self.n_blocks == 3:
            out = self.layer2(out)
            out = self.layer3(out)
            return out
        return out

    def forward(self, x):
        out = self.forward_no_linear(x)
        out = F.avg_pool2d(out, 4)
        out = out.view(out.size(0), -1)
        out = self.linear(out)
        return out
