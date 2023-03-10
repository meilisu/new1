import torch
import torch.nn.functional as F
import torch.nn as nn
import math
from collections import OrderedDict

#-------------------------------------------------#
#   卷积块
#   CONV+BATCHNORM+LeakyReLU
#   in_channel：输入数据的通道数；out_channel：输出数据的通道数；stride：步长，默认为1；
#   kernel_size: 卷积核大小，kernel_size=2,意味着卷积大小(2,2)，kernel_size=（2,3），意味着卷积大小（2，3）即非正方形卷积
#-------------------------------------------------#
class BasicConv(nn.Module):
    def __init__(self, in_channels, out_channels, kernel_size, stride=1):
        super(BasicConv, self).__init__()

        self.conv = nn.Conv2d(in_channels, out_channels, kernel_size, stride, kernel_size//2, bias=False)#bias=False卷积运算是否需要偏置bias，默认为False
        self.bn = nn.BatchNorm2d(out_channels)
        self.activation = nn.LeakyReLU(0.1)



    def forward(self, x):
        
        x = self.bn(x)
        x = self.activation(x)
        return x
#---------------------------------------------------#
#   CSPdarknet53-tiny的结构块
#   存在一个大残差边
#   这个大残差边绕过了很多的残差结构
#   python中//表示结果向下取整，/表示结果向上取整
#---------------------------------------------------#
class Resblock_body(nn.Module):
    def __init__(self, in_channels, out_channels):
        super(Resblock_body, self).__init__()
        self.out_channels = out_channels
#   在YOLOV4中，只有kernel=1和3两种情况，1的时候padding为0,3的时候padding为1;padding零填充
        self.conv1 = BasicConv(in_channels, out_channels, 3)

        self.conv2 = BasicConv(out_channels//2, out_channels//2, 3)#通道数减半
        self.conv3 = BasicConv(out_channels//2, out_channels//2, 3)#通道数减半            ??为什么有两个out_channels

        self.conv4 = BasicConv(out_channels, out_channels, 1)
        self.maxpool = nn.MaxPool2d([2,2],[2,2])


    def forward(self, x): #重写父类方法
        x = self.conv1(x)
        route = x
        c = self.out_channels
        x = torch.split(x, c//2, dim=1)[1]#按照x这个维度去分，每大块包含c//2个小块????这里x是conv1，那conv1是几 
        x = self.conv2(x)
        route1 = x
        x = self.conv3(x)
        x = torch.cat([x,route1], dim = 1) 
        x = self.conv4(x)
        feat = x

        x = torch.cat([route, x], dim=1)
        x = self.maxpool(x)
        return x,feat
class SELayer(nn.Module):
    def __init__(self, channel, reduction=16):
        super(SELayer, self).__init__()
        self.avg_pool = nn.AdaptiveAvgPool2d(1)
        self.fc = nn.Sequential(
            nn.Linear(channel, channel // reduction, bias=False),
            nn.ReLU(inplace=True),
            nn.Linear(channel // reduction, channel, bias=False),
            nn.Sigmoid()
        )

    def forward(self, x):
        b, c, _, _ = x.size()
        y = self.avg_pool(x).view(b, c)
        y = self.fc(y).view(b, c, 1, 1)
        return x * y.expand_as(x)


class SpatialAttention(nn.Module):
    def __init__(self, kernel_size=7):
        super(SpatialAttention, self).__init__()

        assert kernel_size in (3, 7), 'kernel size must be 3 or 7'
        padding = 3 if kernel_size == 7 else 1

        self.conv1 = nn.Conv2d(2, 1, kernel_size, padding=padding, bias=False)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        avg_out = torch.mean(x, dim=1, keepdim=True)
        max_out, _ = torch.max(x, dim=1, keepdim=True)
        y = torch.cat([avg_out, max_out], dim=1)
        y = self.conv1(y)
        y= self.sigmoid(y)
        return y.expand_as(x)*x

class CSPDarkNet(nn.Module):
    def __init__(self):
        super(CSPDarkNet, self).__init__()
        self.conv1 = BasicConv(3, 32, kernel_size=3, stride=2)
        self.conv2 = BasicConv(32, 64, kernel_size=3, stride=2)

        self.resblock_body1 =  Resblock_body(64, 64)
        self.resblock_body2 =  Resblock_body(128, 128)
        self.resblock_body3 =  Resblock_body(256, 256)
        self.conv3 = BasicConv(512, 512, kernel_size=3)

        # self.se_1 = SELayer(64)       SE模块
        # self.se_2 = SELayer(128)
        # self.se_3 = SELayer(256)
        # self.se_4 = SELayer(384)
        # self.se_5 = SELayer(512)
        # self.sa = SpatialAttention()



        self.num_features = 1
        # 进行权值初始化
        #是来对相应的参数的初始化的过程的。进行参数的初始化是会来加快模型收敛速度的。先从self.modules()中遍历每一层，然后判断更层属于什么类型，是否是Conv2d，
        #是否是BatchNorm2d，然后根据不同类型的层，设定不同的权值初始化方法
        for m in self.modules(): ## 依次来返回模型中的各个层的
            if isinstance(m, nn.Conv2d):## 判断是否是相同的实例对象的
                n = m.kernel_size[0] * m.kernel_size[1] * m.out_channels
                m.weight.data.normal_(0, math.sqrt(2. / n)) ## m.weight.data是对应的卷积核参数
            elif isinstance(m, nn.BatchNorm2d):
                m.weight.data.fill_(1) ## 对全部的权重初始化为1
                m.bias.data.zero_() ## 对权重进行初始化为0


    def forward(self, x):
        x = self.conv1(x)
        x = self.conv2(x)#64
        x, _    = self.resblock_body1(x)#128
        x = self.se_2(x)
        x = self.sa(x)
        x, _    = self.resblock_body2(x)
        feat0 = x
        x = self.se_3(x)
        x = self.sa(x)
        x, feat1    = self.resblock_body3(x)
        x = self.se_5(x)
        x = self.sa(x)
        x = self.conv3(x)
        feat2 = x
        return feat0,feat1,feat2
        # return feat1, feat2

def darknet53_tiny(pretrained, **kwargs):
    model = CSPDarkNet()
    if pretrained:
        if isinstance(pretrained, str):
            model.load_state_dict(torch.load(pretrained))
        else:
            raise Exception("darknet request a pretrained path. got [{}]".format(pretrained))
    return model
