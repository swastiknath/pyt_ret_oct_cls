import argparse
import json
import os
import torch
import logging
import sys
import torch.nn.functional as F
import sagemaker_containers
import torch.optim as optim
import torch.utils.data
import torch.nn as nn
import torchvision
from torchvision import datasets, models, transforms


logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.addHandler(logging.StreamHandler(sys.stdout))

from model_resnet import Resnet

def model_fn(model_dir):
    """Load the PyTorch model from the `model_dir` directory."""
    print("Loading model.")

    # First, load the parameters used to create the model.
    model_info = {}
    model_info_path = os.path.join(model_dir, 'model_info.pth')
    with open(model_info_path, 'rb') as f:
        model_info = torch.load(f)

    print("model_info: {}".format(model_info))

    # Determine the device and construct the model.
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = Resnet(model_info['output_dim'])

    # Loading the stored model parameters.
    model_path = os.path.join(model_dir, 'model.pth')
    with open(model_path, 'rb') as f:
        model.load_state_dict(torch.load(f))

    # set to eval mode, could use no_grad
    model.to(device).eval()

    print("Done loading model.")
    return model

# Gets training data in batches from S3
def _get_train_data_loader(batch_size, training_dir, test_dir):
    print("Get train data loader.")
    num_workers = 0

    image_transformer = transforms.Compose([transforms.Resize((224, 224)), 
                                            
                                            transforms.ToTensor()])
    
    train_data = datasets.ImageFolder(training_dir, transform=image_transformer)
    test_data = datasets.ImageFolder(test_dir, transform=image_transformer)
    
    train_loader = torch.utils.data.DataLoader(train_data, 
                                           batch_size=batch_size, 
                                           num_workers=num_workers, 
                                           shuffle=True)
    
    test_loader = torch.utils.data.DataLoader(test_data, 
                                           batch_size=batch_size, 
                                           num_workers=num_workers, 
                                           shuffle=True)
    
    return train_loader, test_loader

def test(model, test_loader, device, criterion):
    model.eval()
    test_loss = 0
    correct = 0
    with torch.no_grad():
        for data, target in test_loader:
            data, target = data.to(device), target.to(device)
            output = model(data)
            test_loss += criterion(output, target).item()  # sum up batch loss
            pred = output.max(1, keepdim=True)[1]  # get the index of the max log-probability
            correct += pred.eq(target.view_as(pred)).sum().item()

    test_loss /= len(test_loader.dataset)
    logger.info('Test set: Average loss: {:.4f}, Accuracy: {}/{} ({:.0f}%)\n'.format(
        test_loss, correct, len(test_loader.dataset),
        100. * correct / len(test_loader.dataset)))



def train(model, train_loader, test_loader, epochs, criterion, optimizer, device):
    """
    This is the training method that is called by the PyTorch training script. The parameters
    passed are as follows:
    model        - The PyTorch model that we wish to train.
    train_loader - The PyTorch DataLoader that should be used during training.
    epochs       - The total number of epochs to train for.
    criterion    - The loss function used for training. 
    optimizer    - The optimizer to use during training.
    device       - Where the model and data should be loaded (gpu or cpu).
    """
        
    for epoch in range(1, epochs + 1):
        model.train() # Making sure that the model is in training mode.

        total_loss = 0

        for batch_idx, (data, label) in enumerate(train_loader):
            # getting the data
            batch_x = data.to(device)
            batch_y = label.to(device)

            optimizer.zero_grad()

            # get predictions from model
            y_pred = model(batch_x)
            
            # perform backprop
            loss = criterion(y_pred, batch_y)
            loss.backward()
            optimizer.step()
            
            total_loss += loss.data.item()
            
            if batch_idx % 20 == 0:
                logger.info('Train Epoch: {} [{}/{} ({:.0f}%)] Loss: {:.6f}'.format(
                    epoch, batch_idx * len(data), len(train_loader.sampler),
                    100. * batch_idx / len(train_loader), loss.item()))
                total_loss = 0
#                 logger.info("Epoch {} : Batch {} : Train Batch Loss: {} Loss: {:.6f}".format(epoch,batch+1, total_loss/20, loss.item()))
                               
    test(model, test_loader, device, criterion)

if __name__ == '__main__':
    

    parser = argparse.ArgumentParser()

    parser.add_argument('--output-data-dir', type=str, default=os.environ['SM_OUTPUT_DATA_DIR'])
    parser.add_argument('--model-dir', type=str, default=os.environ['SM_MODEL_DIR'])
    parser.add_argument('--data-dir', type=str, default=os.environ['SM_CHANNEL_TRAIN'])
    parser.add_argument('--valid-dir', type=str, default=os.environ['SM_CHANNEL_VALIDATION'])
    
    # Training Parameters, given
    parser.add_argument('--batch-size', type=int, default=20, metavar='N',
                        help='input batch size for training (default: 20)')
    parser.add_argument('--epochs', type=int, default=2, metavar='N',
                        help='number of epochs to train (default: 2)')
    parser.add_argument('--seed', type=int, default=1, metavar='S',
                        help='random seed (default: 1)')
    parser.add_argument('--lr', type=float, default=0.001, metavar='L',
                        help='Learning Rate of SGD Optimization')
    
    parser.add_argument('--output_dim', type=int, default=3, metavar='O', 
                        help = 'output dimension (default: 3)')
    
    # args holds all passed-in arguments
    args = parser.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print("Using device {}.".format(device))

    torch.manual_seed(args.seed)

    # Load the training data.
    train_loader, test_loader = _get_train_data_loader(args.batch_size, args.data_dir, args.valid_dir)

    model = Resnet(args.output_dim).to(device)

#     Defining an optimizer and loss function for training
    criterion = nn.CrossEntropyLoss()

    optimizer = optim.SGD(model.parameters(), lr=0.001)

    # Trains the model (given line of code, which calls the above training function)
    train(model, train_loader, test_loader, args.epochs, criterion, optimizer, device)

    model_info_path = os.path.join(args.model_dir, 'model_info.pth')
    with open(model_info_path, 'wb') as f:
        model_info = {
            'output_dim': args.output_dim,
        }
        torch.save(model_info, f)
        
    

	# Save the model parameters
    model_path = os.path.join(args.model_dir, 'model.pth')
    with open(model_path, 'wb') as f:
        torch.save(model.cpu().state_dict(), f)