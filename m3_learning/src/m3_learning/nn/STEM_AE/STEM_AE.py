import torch
import torch.nn as nn
import torch.optim as optim
from os.path import join as pjoin
from torch.utils.data import Dataset, DataLoader
from ..Regularization.Regularizers import ContrastiveLoss, DivergenceLoss
from tqdm import tqdm
from ...util.file_IO import make_folder
import torch.nn.functional as F

class ConvAutoencoder():

    def __init__(self,
                 encoder_step_size,
                 pooling_list,
                 decoder_step_size,
                 upsampling_list,
                 embedding_size,
                 conv_size,
                 device,
                 learning_rate=3e-5,
                 ):
        self.encoder_step_size = encoder_step_size
        self.pooling_list = pooling_list
        self.decoder_step_size = decoder_step_size
        self.upsampling_list = upsampling_list
        self.embedding_size = embedding_size
        self.conv_size = conv_size
        self.device = device
        self.learning_rate = learning_rate

        self.compile_model()

    def compile_model(self):
        self.encoder = Encoder(
            original_step_size=self.encoder_step_size,
            pooling_list=self.pooling_list,
            embedding_size=self.embedding_size,
            conv_size=self.conv_size,
        ).to(self.device)
        self.decoder = Decoder(
            original_step_size=self.decoder_step_size,
            upsampling_list=self.upsampling_list,
            embedding_size=self.embedding_size,
            conv_size=self.conv_size,
            pooling_list=self.pooling_list,
        ).to(self.device)
        self.autoencoder = AutoEncoder(
            self.encoder, self.decoder).to(self.device)

        self.optimizer = optim.Adam(
            self.autoencoder.parameters(), lr=self.learning_rate
        )

        self.autoencoder.type(torch.float32)

    def Train(self,
              data,
              max_learning_rate=1e-4,
              coef_1=0,
              coef_2=0,
              coef_3=0,
              seed=12,
              epochs=100,
              with_scheduler=True,
              ln_parm=1,
              epoch_=None,
              folder_path='./',
              batch_size=32,
              best_train_loss=None):

        make_folder(folder_path)

        # set seed
        torch.manual_seed(seed)

        # builds the dataloader
        self.DataLoader_ = DataLoader(data.reshape(-1,256,256), batch_size=32, shuffle=True)

        # option to use the learning rate scheduler
        if with_scheduler:
            scheduler = torch.optim.lr_scheduler.CyclicLR(
                self.optimizer, base_lr=self.learning_rate, max_lr=max_learning_rate, step_size_up=15, cycle_momentum=False)
        else:
            scheduler = None

        # set the number of epochs
        N_EPOCHS = epochs

        # initializes the best train loss
        if best_train_loss == None:
            best_train_loss = float('inf')

        # initialize the epoch counter
        if epoch_ is None:
            start_epoch = 0
        else:
            start_epoch = epoch_+1

        # training loop
        for epoch in range(start_epoch, N_EPOCHS):

            train = self.loss_function(
                self.DataLoader_, coef_1, coef_2, coef_3, ln_parm)
            train_loss = train
            train_loss /= len(self.DataLoader_)

            print('.............................')

          #  schedular.step()
            if best_train_loss > train_loss:
                best_train_loss = train_loss
                patience_counter = 1
                checkpoint = {
                    "net": self.autoencoder.state_dict(),
                    'optimizer': self.optimizer.state_dict(),
                    "epoch": epoch,
                    "encoder": self.encoder.state_dict(),
                    'decoder': self.decoder.state_dict(),
                }
                if epoch >= 0:
                    lr_ = format(self.optimizer.param_groups[0]['lr'], '.5f')
                    file_path = folder_path + 'Weight_' +\
                        f'epoch:{epoch:04d}_l1coef:{coef_1:.4f}'+'_lr:'+lr_ +\
                        f'_trainloss:{train_loss:.4f}.pkl'
                    torch.save(checkpoint, file_path)

            if scheduler is not None:
                scheduler.step()

    def loss_function(self,
                      train_iterator,
                      coef=0,
                      coef1=0,
                      coef2=0,
                      ln_parm=1,
                      beta=None):

        # set the train mode
        self.autoencoder.train()

        # loss of the epoch
        train_loss = 0
        con_l = ContrastiveLoss(coef1).to(self.device)

        for x in tqdm(train_iterator, leave=True, total=len(train_iterator)):

            x = x.to(self.device, dtype=torch.float)

            maxi_ = DivergenceLoss(x.shape[0], coef2).to(self.device)

            # update the gradients to zero
            self.optimizer.zero_grad()

            if beta is None:
                embedding = self.encoder(x)
            else:
                embedding, sd, mn = self.encoder(x)

            reg_loss_1 = coef * \
                torch.norm(embedding, ln_parm).to(self.device)/x.shape[0]

            if reg_loss_1 == 0:

                reg_loss_1 = 0.5

            predicted_x = self.decoder(embedding)

            contras_loss = con_l(embedding)
            maxi_loss = maxi_(embedding)

            # reconstruction loss
            loss = F.mse_loss(x, predicted_x, reduction='mean')

            loss = loss + reg_loss_1 + contras_loss - maxi_loss

            # backward pass
            train_loss += loss.item()
            loss.backward()
            # update the weights
            self.optimizer.step()

        return train_loss


class ConvBlock(nn.Module):
    """Convolutional Block with 3 convolutional layers, 1 layer normalization layer with ReLU and ResNet

    Args:
        nn (nn.Module): Torch module class
    """

    def __init__(self, t_size, n_step):
        """Initializes the convolutional block

        Args:
            t_size (int): Size of the convolution kernel
            n_step (int): Input shape of normalization layer
        """

        super(ConvBlock, self).__init__()
        self.cov1d_1 = nn.Conv2d(
            t_size, t_size, 3, stride=1, padding=1, padding_mode="zeros"
        )
        self.cov1d_2 = nn.Conv2d(
            t_size, t_size, 3, stride=1, padding=1, padding_mode="zeros"
        )
        self.cov1d_3 = nn.Conv2d(
            t_size, t_size, 3, stride=1, padding=1, padding_mode="zeros"
        )
        self.norm_3 = nn.LayerNorm(n_step)
        self.relu_4 = nn.ReLU()

    def forward(self, x):
        """Forward pass of the convolutional block

        Args:
            x (Tensor): Input tensor

        Returns:
            Tensor: output tensor
        """

        x_input = x
        out = self.cov1d_1(x)
        out = self.cov1d_2(out)
        out = self.cov1d_3(out)
        out = self.norm_3(out)
        out = self.relu_4(out)
        out = out.add(x_input)

        return out


class IdentityBlock(nn.Module):

    """Identity Block with 1 convolutional layers, 1 layer normalization layer with ReLU"""

    def __init__(self, t_size, n_step):
        """Initializes the identity block

        Args:
            t_size (int): Size of the convolution kernel
            n_step (int): Input shape of normalization layer
        """

        super(IdentityBlock, self).__init__()
        self.cov1d_1 = nn.Conv2d(
            t_size, t_size, 3, stride=1, padding=1, padding_mode="zeros"
        )
        self.norm_1 = nn.LayerNorm(n_step)
        self.relu = nn.ReLU()

    def forward(self, x):
        """Forward pass of the identity block

        Args:
            x (Tensor): Input tensor

        Returns:
            Tensor: output tensor
        """

        x_input = x
        out = self.cov1d_1(x)
        out = self.norm_1(out)
        out = self.relu(out)

        return out


class Encoder(nn.Module):
    """Encoder block

    Args:
        nn (nn.Module): Torch module class
    """

    def __init__(self, original_step_size, pooling_list, embedding_size, conv_size):
        """Build the encoder

        Args:
            original_step_size (Int): the x and y size of input image
            pooling_list (List): the list of parameter for each 2D MaxPool layer
            embedding_size (Int): the value for number of channels
            conv_size (Int): the value of filters number goes to each block
        """

        super(Encoder, self).__init__()

        blocks = []

        self.input_size_0 = original_step_size[0]
        self.input_size_1 = original_step_size[1]

        number_of_blocks = len(pooling_list)

        blocks.append(ConvBlock(t_size=conv_size,
                                n_step=original_step_size))
        blocks.append(IdentityBlock(
            t_size=conv_size, n_step=original_step_size))
        blocks.append(nn.MaxPool2d(
            pooling_list[0], stride=pooling_list[0]))

        for i in range(1, number_of_blocks):
            original_step_size = [
                original_step_size[0] // pooling_list[i - 1],
                original_step_size[1] // pooling_list[i - 1],
            ]
            blocks.append(ConvBlock(t_size=conv_size,
                                    n_step=original_step_size))
            blocks.append(
                IdentityBlock(t_size=conv_size, n_step=original_step_size)
            )
            blocks.append(nn.MaxPool2d(
                pooling_list[i], stride=pooling_list[i]))

        self.block_layer = nn.ModuleList(blocks)
        self.layers = len(blocks)

        original_step_size = [
            original_step_size[0] // pooling_list[-1],
            original_step_size[1] // pooling_list[-1],
        ]
        input_size = original_step_size[0] * original_step_size[1]

        self.cov2d = nn.Conv2d(
            1, conv_size, 3, stride=1, padding=1, padding_mode="zeros"
        )
        self.cov2d_1 = nn.Conv2d(
            conv_size, 1, 3, stride=1, padding=1, padding_mode="zeros"
        )

        self.relu_1 = nn.ReLU()

        self.dense = nn.Linear(input_size, embedding_size)

    def forward(self, x):
        """Forward pass of the encoder

        Args:
            x (Tensor): Input tensor

        Returns:
            Tensor: output tensor
        """
        out = x.view(-1, 1, self.input_size_0, self.input_size_1)
        out = self.cov2d(out)
        for i in range(self.layers):
            out = self.block_layer[i](out)
        out = self.cov2d_1(out)
        out = torch.flatten(out, start_dim=1)
        out = self.dense(out)
        selection = self.relu_1(out)

        return selection


class Decoder(nn.Module):
    def __init__(
        self,
        original_step_size,
        upsampling_list,
        embedding_size,
        conv_size,
        pooling_list,
    ):
        """Decoder block

        Args:
            original_step_size (Int): the x and y size of input image
            upsampling_list (Int): the list of parameter for each 2D upsample layer
            embedding_size (Int): the value for number of channels
            conv_size (Int): the value of filters number goes to each block
            pooling_list (List): the list of parameter for each 2D MaxPool layer
        """

        super(Decoder, self).__init__()
        self.input_size_0 = original_step_size[0]
        self.input_size_1 = original_step_size[1]
        self.dense = nn.Linear(
            embedding_size, original_step_size[0] * original_step_size[1]
        )
        self.cov2d = nn.Conv2d(
            1, conv_size, 3, stride=1, padding=1, padding_mode="zeros"
        )
        self.cov2d_1 = nn.Conv2d(
            conv_size, 1, 3, stride=1, padding=1, padding_mode="zeros"
        )

        blocks = []
        number_of_blocks = len(pooling_list)
        blocks.append(ConvBlock(t_size=conv_size,
                                n_step=original_step_size))
        blocks.append(IdentityBlock(
            t_size=conv_size, n_step=original_step_size))
        for i in range(number_of_blocks):
            blocks.append(
                nn.Upsample(
                    scale_factor=upsampling_list[i],
                    mode="bilinear",
                    align_corners=True,
                )
            )
            original_step_size = [
                original_step_size[0] * upsampling_list[i],
                original_step_size[1] * upsampling_list[i],
            ]
            blocks.append(ConvBlock(t_size=conv_size,
                                    n_step=original_step_size))
            blocks.append(
                IdentityBlock(t_size=conv_size, n_step=original_step_size)
            )

        self.block_layer = nn.ModuleList(blocks)
        self.layers = len(blocks)

        self.output_size_0 = original_step_size[0]
        self.output_size_1 = original_step_size[1]

    def forward(self, x):
        """Forward pass of the identity block

        Args:
            x (Tensor): Input tensor

        Returns:
            Tensor: output tensor
        """

        out = self.dense(x)
        out = out.view(-1, 1, self.input_size_0, self.input_size_1)

        out = self.cov2d(out)
        for i in range(self.layers):
            out = self.block_layer[i](out)
        out = self.cov2d_1(out)
        output = out.view(-1, self.output_size_0, self.output_size_1)

        return output


class AutoEncoder(nn.Module):
    def __init__(self, enc, dec):
        """AutoEncoder model

        Args:
            enc (nn.Module): Encoder block
            dec (nn.Module): Decoder block
        """
        super().__init__()

        self.enc = enc
        self.dec = dec

    def forward(self, x):
        """Forward pass of the autoencoder

        Args:
            x (Tensor): Input tensor

        Returns:
            Tensor: output tensor
        """

        embedding = self.enc(x)

        predicted = self.dec(embedding)

        return predicted