import argparse
from path import Path

import torch
import torch.backends.cudnn as cudnn
import torch.nn.functional as F
import models
from tqdm import tqdm

import torchvision.transforms as transforms
import flow_transforms
from imageio import imread, imwrite
import numpy as np
from util import flow2rgb
import os
import cv2

model_names = sorted(name for name in models.__dict__
                     if name.islower() and not name.startswith("__"))


parser = argparse.ArgumentParser(description='PyTorch FlowNet inference on a folder of img pairs',
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument('data', metavar='DIR',
                    help='path to images folder, image names must match \'[name]0.[ext]\' and \'[name]1.[ext]\'')
parser.add_argument('pretrained', metavar='PTH', help='path to pre-trained model')
parser.add_argument('--output', '-o', metavar='DIR', default=None,
                    help='path to output folder. If not set, will be created in data folder')
parser.add_argument('--output-value', '-v', choices=['raw', 'vis', 'both'], default='both',
                    help='which value to output, between raw input (as a npy file) and color vizualisation (as an image file).'
                    ' If not set, will output both')
parser.add_argument('--div-flow', default=20, type=float,
                    help='value by which flow will be divided. overwritten if stored in pretrained file')
parser.add_argument("--img-exts", metavar='EXT', default=['png', 'jpg', 'bmp', 'ppm'], nargs='*', type=str,
                    help="images extensions to glob")
parser.add_argument('--max_flow', default=None, type=float,
                    help='max flow value. Flow map color is saturated above this value. If not set, will use flow map\'s max value')
parser.add_argument('--upsampling', '-u', choices=['nearest', 'bilinear'], default=None, help='if not set, will output FlowNet raw input,'
                    'which is 4 times downsampled. If set, will output full resolution flow map, with selected upsampling')
parser.add_argument('--bidirectional', action='store_true', help='if set, will output invert flow (from 1 to 0) along with regular flow')

device = torch.device("cuda") if torch.cuda.is_available() else torch.device("cpu")


@torch.no_grad()
def main():
    global args, save_path
    args = parser.parse_args()

    if args.output_value == 'both':
        output_string = "raw output and RGB visualization"
    elif args.output_value == 'raw':
        output_string = "raw output"
    elif args.output_value == 'vis':
        output_string = "RGB visualization"
    print("=> will save " + output_string)
    data_dir = Path(args.data)
    print("=> fetching img pairs in '{}'".format(args.data))
    if args.output is None:
        save_path = data_dir/'flow'
    else:
        save_path = Path(args.output)
    print('=> will save everything to {}'.format(save_path))
    save_path.makedirs_p()
    # Data loading code
    input_transform = transforms.Compose([
        flow_transforms.ArrayToTensor(),
        transforms.Normalize(mean=[0,0,0], std=[255,255,255]),
        transforms.Normalize(mean=[0.411,0.432,0.45], std=[1,1,1])
    ])

    # Make sure the folder has extracted images in the format of "name + num + extension", where num start from 0, 1, 2, ...
    # Currently only test on MPI-Sintel, which comprises .png images
    img_pairs = []
    
    img_list = os.listdir(data_dir)
    img_list = sorted(img_list)
    #img_list.sort()
    img_list = img_list[1:]
    print(img_list)
    #for ext in args.img_exts:
    for ind in range(len(img_list)-1):
        img1 = img_list[ind]
        print("img1: ", img1)

        #test_files = data_dir.files('*1.{}'.format(ext))
        img1_name, ext = img1.split('.')
        basename, num = img1_name.split('_')
        
        img2 = '.'.join(['_'.join([basename,  str(int(num)+1).zfill(4)]), ext])
        print("img 2: ", img2)
    
        img_pairs.append([data_dir + '/' + img1, data_dir + '/' + img2])
        #print("Test files: ")
        #print(test_files)
        #for file in test_files:
            #img_pair = file.parent / (file.namebase[:-1] + '2.{}'.format(ext))
            #print("file: ")
            #print(file)
            #print("img_pair: ")
            #print(img_pair)
            #if img_pair.isfile():
            #    img_pairs.append([file, img_pair])

    print('{} samples found'.format(len(img_pairs)))
    #print(img_pairs)
    # create model
    network_data = torch.load(args.pretrained)
    print("=> using pre-trained model '{}'".format(network_data['arch']))
    model = models.__dict__[network_data['arch']](network_data).to(device)
    model.eval()
    cudnn.benchmark = True

    if 'div_flow' in network_data.keys():
        args.div_flow = network_data['div_flow']

    output = data_dir + '/flow/' + 'output.mp4'
    print(output)
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(output, fourcc, 10.0, (640, 480))
    
    for (img1_file, img2_file) in tqdm(img_pairs):

        img1 = input_transform(imread(img1_file))
        img2 = input_transform(imread(img2_file))
        input_var = torch.cat([img1, img2]).unsqueeze(0)

        if args.bidirectional:
            # feed inverted pair along with normal pair
            inverted_input_var = torch.cat([img2, img1]).unsqueeze(0)
            input_var = torch.cat([input_var, inverted_input_var])

        input_var = input_var.to(device)
        # compute output
        output = model(input_var)
        if args.upsampling is not None:
            output = F.interpolate(output, size=img1.size()[-2:], mode=args.upsampling, align_corners=False)
        for suffix, flow_output in zip(['flow', 'inv_flow'], output):
            print(img1_file.namebase)
            filename = save_path/'{}{}'.format(img1_file.namebase, suffix)
            print(filename)
            if args.output_value in['vis', 'both']:
                rgb_flow = flow2rgb(args.div_flow * flow_output, max_value=args.max_flow)
                to_save = (rgb_flow * 255).astype(np.uint8).transpose(1,2,0)
                
                imwrite(filename + '.png', to_save)

                out.write(to_save)
                cv2.imshow('video', to_save)
                if (cv2.waitKey(1) & 0xFF) == ord('q'): break 
            if args.output_value in ['raw', 'both']:
                # Make the flow map a HxWx2 array as in .flo files
                to_save = (args.div_flow*flow_output).cpu().numpy().transpose(1,2,0)
                np.save(filename + '.npy', to_save)
    out.release()
    cv2.destroyAllWindows()

if __name__ == '__main__':
    main()
