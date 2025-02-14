import sys
import os.path as osp
import os
import random
from torch_geometric.data.data import Data
from torch_geometric.data.dataset import Dataset
from torch_geometric.nn import GCNConv, TopKPooling, SAGEConv, EdgePooling
from torch_geometric.nn import global_mean_pool as gap, global_max_pool as gmp
from torch_geometric.data.in_memory_dataset import InMemoryDataset
from tqdm import tqdm
import time
import gc
import argparse
sys.path.append(os.path.realpath('.'))

from src.methods import dataset_analysis
from src.classes import LncRNA_Protein_Interaction_dataset_1hop_1220_InMemory

from torch_geometric.data import DataLoader

import torch
import torch.nn.functional as F
from torch.optim import *


def parse_args():
    parser = argparse.ArgumentParser(description="generate_dataset.")
    parser.add_argument('--trainingName', help='the name of this training')
    parser.add_argument('--trainingDatasetName',  help='the name of this object')
    parser.add_argument('--testingDatasetName',  help='the name of this object')
    parser.add_argument('--inMemory', default=1, type = int, help='in memory dataset or not')
    parser.add_argument('--interactionDatasetName', default='NPInter2', help='raw interactions dataset')
    parser.add_argument('--fold', type=int, help='this is part of cross validation, the ith fold')
    parser.add_argument('--epochNumber', default=50, type=int, help='number of training epoch')
    parser.add_argument('--hopNumber', default=1, type=int , help='hop number of subgraph')
    parser.add_argument('--node2vecWindowSize', default=5, type=int, help='node2vec window size')
    # parser.add_argument('--crossValidation', default=1, type=int, help='do cross validation')
    # parser.add_argument('--foldNumber', default=5, type=int, help='fold number of cross validation')
    parser.add_argument('--initialLearningRate', default=0.001, type=float, help='Initial learning rate')
    parser.add_argument('--l2WeightDecay', default=0.001, type=float, help='L2 weight')
    parser.add_argument('--batchSize', default=200, type=int, help='batch size')

    return parser.parse_args()


class Net_1_onlyOneOutput(torch.nn.Module):
    def __init__(self, num_node_features, num_of_classes=2):
        super(Net_1_onlyOneOutput, self).__init__()
        self.conv1 = SAGEConv(num_node_features, 128)
        self.pool1 = TopKPooling(128, ratio=0.5)
        self.conv2 = SAGEConv(128, 128)
        self.pool2 = TopKPooling(128, ratio=0.5)
        self.conv3 = SAGEConv(128, 128)
        self.pool3 = TopKPooling(128, ratio=0.5)

        self.lin1 = torch.nn.Linear(256, 128)
        self.lin2 = torch.nn.Linear(128, 64)
        self.lin3 = torch.nn.Linear(64, 1)
    
    def forward(self, data):
        x, edge_index, batch = data.x, data.edge_index, data.batch

        x = F.relu(self.conv1(x, edge_index))
        x, edge_index, _, batch, _, _ = self.pool1(x, edge_index, None, batch)
        x1 = torch.cat([gmp(x, batch), gap(x, batch)], dim=1)

        x = F.relu(self.conv2(x, edge_index))
        x, edge_index, _, batch, _, _ = self.pool2(x, edge_index, None, batch)
        x2 = torch.cat([gmp(x, batch), gap(x, batch)], dim=1)

        x = F.relu(self.conv3(x, edge_index))
        x, edge_index, _, batch, _, _ = self.pool3(x, edge_index, None, batch)
        x3 = torch.cat([gmp(x, batch), gap(x, batch)], dim=1)

        x = x1 + x2 + x3

        x = F.relu(self.lin1(x))
        x = F.dropout(x, p=0.5, training=self.training)
        x = F.relu(self.lin2(x))
        x = self.lin3(x)
        x = torch.sigmoid(x)

        return x


def train():
    model.train()
    loss_all = 0
    for data in train_loader:
        data = data.to(device)
        optimizer.zero_grad()
        output = model(data)
        target = data.y.type(torch.FloatTensor).view(len(data.y),1).to(device)
        loss = F.binary_cross_entropy(output, target)
        loss.backward()
        loss_all += data.num_graphs * loss.item()
        optimizer.step()
    return loss_all / len(train_dataset)


def Accuracy_Precision_Sensitivity_Specificity_MCC(model, loader, device):
    model.eval()
    
    TP = 0
    TN = 0
    FP = 0
    FN = 0
    for data in loader:
        data = data.to(device)
        pred = model(data)
        for index in range(len(pred)):
            if pred[index] > 0.5:
                if data.y[index] == 1:
                    TP += 1
                else:
                    FP += 1
            else:
                if data.y[index] == 1:
                    FN += 1
                else:
                    TN += 1
    print('TP: %d, FN: %d, TN: %d, FP: %d' % (TP, FN, TN, FP))
    if (TP + TN + FP + FN) != 0:
        Accuracy = (TP + TN) / (TP + TN + FP + FN)
    else:
        Accuracy = 0
    if (TP + FP) != 0:
        Precision = (TP) / (TP + FP)
    else:
        Precision = 0
    if (TP + FN) != 0:
        Sensitivity = (TP) / (TP + FN)
    else:
        Sensitivity = 0
    if (((TP + FP) * (TP + FN) * (TN + FP) * (TN + FN)) ** 0.5) != 0:
        MCC = (TP * TN - FP * FN) / (((TP + FP) * (TP + FN) * (TN + FP) * (TN + FN)) ** 0.5)
    else:
        MCC = 0
    if (FP + TN) != 0:
        Specificity = TN / (FP + TN)
    else:
        Specificity = 0
    return Accuracy, Precision, Sensitivity, Specificity, MCC


if __name__ == "__main__":
    #参数
    args = parse_args()

    training_dataset_path = f'.\\data\\dataset\\{args.trainingDatasetName}'
    testing_dataset_path = f'.\\data\\dataset\\{args.testingDatasetName}'
    # 读取数据集
    if args.inMemory == 0:
        raise Exception("not ready yet")
        train_dataset = Dataset(root=training_dataset_path)
        test_dataset = Dataset(root=testing_dataset_path)
    elif args.inMemory == 1:
        train_dataset = LncRNA_Protein_Interaction_dataset_1hop_1220_InMemory(root=training_dataset_path)
        test_dataset = LncRNA_Protein_Interaction_dataset_1hop_1220_InMemory(root=testing_dataset_path)
    else:
        print(f'--inMemory : {args.inMemory}')
        raise Exception("--inMemory has to be 0 or 1")
    # 打乱数据集
    print('shuffle dataset\n')
    # train_dataset = train_dataset.shuffle()
    # test_dataset = test_dataset.shuffle()
    
    #选择CPU或CUDA
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    # 准备日志
    saving_path = f'result/{args.trainingName}'
    if not osp.exists(saving_path):
        print(f'创建文件夹：{saving_path}')
        os.makedirs(saving_path)
        
     # 迭代次数
    num_of_epoch = args.epochNumber

    # 学习率
    LR = args.initialLearningRate

    # L2正则化系数
    L2_weight_decay = args.l2WeightDecay

    # 日志基本信息写入
    log_path = saving_path + f'/log_{args.fold}.txt'
    result_file = open(file=log_path, mode='w')
    result_file.write(f'training dataset : {args.trainingDatasetName}')
    result_file.write(f'testing dataset : {args.testingDatasetName}')
    result_file.write(f'database：{args.interactionDatasetName}\n')
    result_file.write(f'node2vec_windowSize = {args.node2vecWindowSize}\n')
    result_file.write(f'number of eopch ：{num_of_epoch}\n')
    result_file.write(f'learn rate：initial = {LR}，whenever loss increases, multiply by 0.95\n')
    result_file.write(f'L2 weight decay = {L2_weight_decay}\n')

    # 记录启示时间
    start_time = time.time()
    
    # 创建保存模型的文件夹
    if osp.exists(saving_path + f'/model_{args.fold}_fold'):
        raise Exception('Same fold has been done')
    else:
        print(f'创建文件夹：{saving_path}' + f'/model_{args.fold}_fold')
        os.makedirs(saving_path + f'/model_{args.fold}_fold')
    # 创建模型
    num_of_classes = 2
    
    #检查特征维度
    if(train_dataset.num_node_features != test_dataset.num_node_features):
        raise Exception('训练集和测试集的结点特征维度不一致')

    # 创建模型，优化器
    model = Net_1_onlyOneOutput(train_dataset.num_node_features, num_of_classes).to(device)
    
    optimizer = torch.optim.Adam(model.parameters(), lr=LR, weight_decay=L2_weight_decay)
    # scheduler = lr_scheduler.MultiStepLR(optimizer,milestones=[int(num_of_epoch * 0.2),int(num_of_epoch * 0.8)],gamma = 0.8)
    scheduler = torch.optim.lr_scheduler.ExponentialLR(optimizer=optimizer, gamma=0.95)

    # 训练集和测试集

    print('number of samples in testing dataset：', len(test_dataset), 'number of samples in training dataset：', len(train_dataset))
    print('training dataset')
    dataset_analysis(train_dataset)
    print('testing dataset')
    dataset_analysis(test_dataset)

    train_loader = DataLoader(train_dataset, batch_size=args.batchSize)
    test_loader = DataLoader(test_dataset, batch_size=args.batchSize)

    MCC_max = -1
    epoch_MCC_max = 0
    ACC_MCC_max = 0
    Pre_MCC_max = 0
    Sen_MCC_max = 0
    Spe_MCC_max = 0

    # 训练开始
    loss_last = float('inf')
    for epoch in range(num_of_epoch):
        loss = train()

        # loss增大时,降低学习率
        if loss > loss_last:
            scheduler.step()
        loss_last = loss

        # 训练中评价模型，监视训练过程中的模型变化, 并且写入文件
        if (epoch + 1) % 1 == 0 and epoch != num_of_epoch - 1:
            # 用Accuracy, Precision, Sensitivity, MCC评价模型
            # Accuracy, Precision, Sensitivity ,MCC = Accuracy_Precision_Sensitivity_MCC(model, train_loader, device)
            Accuracy, Precision, Sensitivity, Specificity, MCC = Accuracy_Precision_Sensitivity_Specificity_MCC(model, train_loader, device)
            output = 'Epoch: {:03d}, training dataset, Accuracy: {:.5f}, Precision: {:.5f}, Sensitivity: {:.5f}, Specificity: {:.5f}, MCC: {:.5f}'.format(epoch + 1, Accuracy, Precision, Sensitivity, Specificity, MCC)
            print(output)
            result_file.write(output + '\n')
            # Accuracy, Precision, Sensitivity, MCC = Accuracy_Precision_Sensitivity_MCC(model, test_loader, device)
            Accuracy, Precision, Sensitivity, Specificity, MCC = Accuracy_Precision_Sensitivity_Specificity_MCC(model, test_loader, device)
            output = 'Epoch: {:03d}, testing dataset, Accuracy: {:.5f}, Precision: {:.5f}, Sensitivity: {:.5f}, Specificity: {:.5f}, MCC: {:.5f}'.format(epoch + 1, Accuracy, Precision, Sensitivity, Specificity, MCC)
            print(output)
            result_file.write(output + '\n')
            # 保存模型
            if MCC > MCC_max:
                MCC_max = MCC
                epoch_MCC_max = epoch+1
                ACC_MCC_max = Accuracy
                Pre_MCC_max = Precision
                Sen_MCC_max = Sensitivity
                Spe_MCC_max = Specificity
            network_model_path = saving_path + f'/model_{args.fold}_fold/{epoch+1}'
            torch.save(model.state_dict(), network_model_path)



    # 训练结束，评价模型，并且把结果写入文件
    Accuracy, Precision, Sensitivity, Specificity, MCC = Accuracy_Precision_Sensitivity_Specificity_MCC(model, train_loader, device)
    output = 'result, training dataset, Accuracy: {:.5f}, Precision: {:.5f}, Sensitivity: {:.5f}, Specificity: {:.5f}, MCC: {:.5f}'.format(Accuracy, Precision, Sensitivity, Specificity, MCC)
    print(output)
    result_file.write(output + '\n')
    Accuracy, Precision, Sensitivity, Specificity, MCC = Accuracy_Precision_Sensitivity_Specificity_MCC(model, test_loader, device)
    output = 'result, testing dataset, Accuracy: {:.5f}, Precision: {:.5f}, Sensitivity: {:.5f}, Specificity: {:.5f}, MCC: {:.5f}'.format(Accuracy, Precision, Sensitivity, Specificity, MCC)
    print(output)
    result_file.write(output + '\n')
    if MCC > MCC_max:
                MCC_max = MCC
                epoch_MCC_max = args.epochNumber
                ACC_MCC_max = Accuracy
                Pre_MCC_max = Precision
                Sen_MCC_max = Sensitivity
                Spe_MCC_max = Specificity
    # 把模型存起来
    network_model_path = saving_path + f'/model_{args.fold}_fold/{num_of_epoch}'
    torch.save(model.state_dict(), network_model_path)

    result_file.write('\n')
    output = f'MCC最大的时候的性能：'
    print(output)
    result_file.write(output + '\n')
    output = f'epoch: {epoch_MCC_max}, MCC: {MCC_max}, ACC: {ACC_MCC_max}, Pre: {Pre_MCC_max}, Sen: {Sen_MCC_max}, Spe: {Spe_MCC_max}'
    print(output)
    result_file.write(output + '\n')


    # 完毕
    end_time = time.time()
    print('Time consuming:', end_time - start_time)
    result_file.write('Time consuming:' + str(end_time - start_time) + '\n')
    
    result_file.close()
    
    print('\nexit\n')