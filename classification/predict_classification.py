#!/usr/bin/env python

'''

Predict location on EAP from other models and compares it with validation ones

'''
from __future__ import print_function

import numpy as np
import tensorflow as tf
import os, sys
from os.path import join
import shutil
import csv
import re
import time
import yaml
import json
import pickle

base_folder = os.path.dirname(os.getcwd())
sys.path.insert(0, base_folder)

import MEAutility as MEA

from defaultconfig import *
if os.path.exists(join(base_folder,'config.py')):
    from config import *

from tools import *
from tftools import *


class Prediction:
    def __init__(self, model_path=None, spike_folder=None):

        self.model_path = os.path.normpath(model_path)
        model_info = [f for f in os.listdir(self.model_path) if '.yaml' in f or '.yml' in f][0]
        with open(join(self.model_path, model_info), 'r') as f:
            self.model_info = yaml.load(f)
        # open 1 yaml file and save pitch
        self.all_categories = ['BP', 'BTC', 'ChC', 'DBC', 'LBC', 'MC', 'NBC',
                               'NGC', 'SBC', 'STPC', 'TTPC1', 'TTPC2', 'UTPC']

        self.cell_dict = {}
        for cc in self.all_categories:
            self.cell_dict.update({int(np.argwhere(np.array(self.all_categories) == cc)): cc})

        # Load validation spikes
        self.val_data_dir = join(self.model_path, 'validation_data')
        self.binary_cat = ['EXCIT', 'INHIB']

        self.exc_categories = ['STPC', 'TTPC1', 'TTPC2', 'UTPC']
        self.inh_categories = ['BP', 'BTC', 'ChC', 'DBC', 'LBC', 'MC', 'NBC', 'NGC', 'SBC']

        # Localization model
        self.feat_type = self.model_info['Features']['feature type']
        print('Feature type: ', self.feat_type)
        self.validation_type = self.model_info['Validation']['validation type']
        print('Validation type: ', self.validation_type)
        self.size = self.model_info['CNN']['size']
        print('Network size: ', self.size)
        self.rotation_type = self.model_info['General']['rotation']
        print('Rotation type: ', self.rotation_type)
        self.mea_dim = self.model_info['General']['MEA dimension']

        self.dt = self.model_info['Features']['dt']
        self.threshold_detect = self.model_info['Features']['amp threshold']

        self.inputs = self.model_info['CNN']['inputs']
        self.c1size = self.model_info['CNN']['c1size']  # 1st layer number of features
        self.c2size = self.model_info['CNN']['c2size']  # 2nd layer number of features
        self.ctsize = self.model_info['CNN']['ctsize']
        self.l1depth = self.model_info['CNN']['l1depth']
        self.l2depth = self.model_info['CNN']['l2depth']
        self.fully = self.model_info['CNN']['fully']
        self.out = self.model_info['CNN']['outputs']
        self.classification = self.model_info['General']['classification']

        curr_eval_dir = self.val_data_dir
        validation_run = 0
        if os.path.isdir(self.val_data_dir):
            if self.classification == 'binary':
                self.spikes, self.features, self.loc, self.rot, self.cat, self.mcat = \
                        load_validation_data(self.val_data_dir, load_mcat=True)
            else:
                self.spikes, self.features, self.loc, self.rot, self.cat, = load_validation_data(self.val_data_dir)
                self.mcat = self.cat
            num_spikes = len(self.cat)

            self.cell_dict = {}
            for cc in self.all_categories:
                self.cell_dict.update({int(np.argwhere(np.array(self.all_categories) == cc)): cc})
            val_data = True
            mcat_strings = np.array([self.cell_dict[cc] for cc in self.mcat])

        MEAname = self.model_info['General']['electrode name']
        MEAdims = self.model_info['General']['MEA dimension']
        self.rotation_type = self.model_info['General']['rotation']
        self.electrode_name = MEAname
        self.n_points = self.model_info['General']['n_points']
        self.pitch = self.model_info['General']['pitch']

        # load MEA info
        with open(join(root_folder, 'electrodes', MEAname + '.json')) as meafile:
            elinfo = json.load(meafile)

        x_plane = 0.
        pos = MEA.get_elcoords(x_plane, **elinfo)
        self.mea_pos = pos
        self.mea_dim = MEAdims
        self.num_categories = len(np.unique(self.cat))

        tf.reset_default_graph()

        if val_data:
            print('\nClassifying on validation data from trainel CNN model')
            test_features_tf = tf.constant(self.features, dtype=np.float32)
            test_cat_tf = tf.one_hot(self.cat, depth=self.num_categories, dtype=np.int64)
            # prediction
            test_pred = self.inference(test_features_tf)
            true = tf.argmax(test_cat_tf, 1)
            guessed = tf.argmax(test_pred, 1)
            correct_prediction = tf.equal(guessed, true)

            with tf.Session() as sess:
                saver = tf.train.Saver()
                ckpt = tf.train.get_checkpoint_state(join(self.model_path, 'train', 'run%d' % validation_run))
                if ckpt and ckpt.model_checkpoint_path:
                    relative_model_path_idx = ckpt.model_checkpoint_path.index('models/')
                    relative_model_path = join(data_dir, 'classification',
                                               ckpt.model_checkpoint_path[relative_model_path_idx:])
                    saver.restore(sess, relative_model_path)
                    global_step = ckpt.model_checkpoint_path.split('/')[-1].split('-')[-1]
                else:
                    print('No checkpoint file found')
                    return

                accuracy = tf.reduce_mean(tf.cast(correct_prediction, "float"))
                
                self.acc, self.guess = sess.run([accuracy, guessed], feed_dict={self.keep_prob: 1.0})

            tf.reset_default_graph()
            # pred_cat = [self.pred_dict[cc] for cc in self.guessed[v]]

            print('\n{0:*^60}\n{1:*^60}\n{2:*^60}\n{3:*^60}'.format(
    '','   RESULTS   ',' Accuracies for validation data of trained CNN ',''))
            print('Data from %s' % '/'.join(self.val_data_dir.split(os.sep)[-2:]))
            
            print('\n'+60*'=')
            print('Average validation accuracy: %.2f %%' % (100.*self.acc))
            print(60*'=')

            print('\n{0:*^60}\n'.format(' Cell specific results '))
            for cc in self.cell_dict.values():
                ids = np.argwhere(mcat_strings==cc)
                nncat = len(ids)
                ccacc = float(len(np.where(self.guess[ids]==self.cat[ids])[0]))/nncat
                print('Accuracy for cells of type %s: %.2f %%' 
                      % (cc, 100*ccacc))

            print('\n{0:*^60}\n'.format(' END RESULTS '))


        if spike_folder is not None:
            self.model_type = os.path.normpath(spike_folder).split(os.sep)[-3]
            print('\nClassifying neurons with ', self.model_type, ' model EAPs from spike folder:\n')
            print('%s\n' % spike_folder)
            spikefiles = [f for f in os.listdir(spike_folder) if 'spikes' in f]
            cell_names = ['_'.join(ss.split('_')[3:-1]) for ss in spikefiles]
            self.other_spikes, self.other_loc, self.other_rot, other_cat, oetype,\
                omid, oloaded_cat = load_EAP_data(spike_folder,cell_names,self.all_categories)

            self.other_features = self.return_features(self.other_spikes)
            self.other_cat = []
            other_features_tf = tf.constant(self.other_features, dtype=np.float32)
            for c in other_cat:
                if 'L5b' in c or c in self.exc_categories:
                    self.other_cat.append(0)
                else:
                    self.other_cat.append(1)
            self.other_cat = np.array(self.other_cat)

            other_cat_tf = tf.one_hot(self.other_cat, depth=self.num_categories, dtype=np.int64)
            # prediction
            test_pred = self.inference(other_features_tf)
            true = tf.argmax(other_cat_tf, 1)
            guessed = tf.argmax(test_pred, 1)
            correct_prediction = tf.equal(guessed, true)

            with tf.Session() as sess:
                saver = tf.train.Saver()
                ckpt = tf.train.get_checkpoint_state(join(self.model_path, 'train', 'run%d' % validation_run))
                if ckpt and ckpt.model_checkpoint_path:
                    relative_model_path_idx = ckpt.model_checkpoint_path.index('models/')
                    relative_model_path = join(data_dir, 'classification',
                                               ckpt.model_checkpoint_path[relative_model_path_idx:])
                    saver.restore(sess, relative_model_path)
                    global_step = ckpt.model_checkpoint_path.split('/')[-1].split('-')[-1]
                else:
                    print('No checkpoint file found')
                    return


                accuracy = tf.reduce_mean(tf.cast(correct_prediction, "float"))

                self.other_acc, self.other_guess = sess.run([accuracy, guessed], feed_dict={self.keep_prob: 1.0})

            print('\n{0:*^60}\n{1:*^60}\n{2:*^60}\n{3:*^60}'.format(
    '','   RESULTS   ',' Accuracies for data from spike folder ',''))
            print('Data from %s' % spike_folder)
            
            print('\n'+60*'=')
            print('Average accuracy: %.2f %%' % (100.*self.other_acc))
            print(60*'=')

            print('\n{0:*^60}\n'.format(' Cell specific results '))
            for cc in np.unique(other_cat):
                ids = np.argwhere(other_cat==cc)
                nncat = len(ids)
                ccacc = float(len(np.where(self.other_guess[ids]==self.other_cat[ids])[0]))/nncat
                print('Accuracy for cells of type %s: %.2f %%' 
                      % (cc, 100*ccacc))

            print('\n{0:*^60}\n'.format(' END RESULTS '))

        tf.reset_default_graph()


    def return_features(self, spikes):
        ''' extract features from spikes
        '''
        # Create feature set:
        if self.feat_type == 'Na':
            features = get_EAP_features(spikes, ['Na'], \
                                        dt=self.dt, threshold_detect=self.threshold_detect)
            features = np.array(features['na'])
            self.inputs = 1
        elif self.feat_type == 'NaRep':
            features = get_EAP_features(spikes, ['Na', 'Rep'], \
                                        dt=self.dt, threshold_detect=self.threshold_detect)
            features = np.array([features['na'], features['rep']])
            self.inputs = 2  # fwhm and widths
            # swapaxes to move features at the end
            features = features.swapaxes(0, 1)
            features = features.swapaxes(1, 2)
        elif self.feat_type == 'AW':
            features = get_EAP_features(spikes,['A','W'],\
                                             dt=self.dt,threshold_detect=self.threshold_detect)
            features = np.array([features['amps'],features['widths']])
            self.inputs = 2  # amps and widths
            # swapaxes to move features at the end
            features = features.swapaxes(0, 1)
            features = features.swapaxes(1, 2)
        elif self.feat_type == 'FW':
            features = get_EAP_features(spikes,['F','W'],\
                                             dt=self.dt,threshold_detect=self.threshold_detect)
            features = np.array([features['fwhm'],features['widths']])
            self.inputs = 2  # fwhm and widths
            # swapaxes to move features at the end
            features = features.swapaxes(0, 1)
            features = features.swapaxes(1, 2)
        elif self.feat_type == 'AF':
            features = get_EAP_features(spikes,['A','F'],\
                                             dt=self.dt,threshold_detect=self.threshold_detect)
            features = np.array([features['amps'],features['fwhm']])
            self.inputs = 2  # amps and fwhm
            # swapaxes to move features at the end
            features = features.swapaxes(0, 1)
            features = features.swapaxes(1, 2)
        elif self.feat_type == 'AFW':
            features = get_EAP_features(spikes,['A','F','W'],\
                                             dt=self.dt,threshold_detect=self.threshold_detect)
            features = np.array([features['amps'],features['fwhm'],features['widths']])
            self.inputs = 3  # amps, fwhms and widths
            # swapaxes to move features at the end
            features = features.swapaxes(0, 1)
            features = features.swapaxes(1, 2)
        elif self.feat_type == 'FWRS':
            features = get_EAP_features(spikes,['F','W', 'R', 'S'],\
                                             dt=self.dt,threshold_detect=self.threshold_detect)
            features = np.array([features['fwhm'],features['widths'],features['ratio'],features['speed']])
            self.inputs = 4  # fwhms, widths, ratios, speed
            # swapaxes to move features at the end
            features = features.swapaxes(0, 1)
            features = features.swapaxes(1, 2)
        elif self.feat_type == '3d':
            print('Downsampling spikes...')
            # downsampled_spikes = ss.resample(self.spikes, self.spikes.shape[2] // self.downsampling_factor, axis=2)
            downsampled_spikes = spikes[:, :, ::int(self.downsampling_factor)]
            features = downsampled_spikes
            self.inputs = spikes.shape[2] // self.downsampling_factor
            print('Done')

        return features


    def inference(self, xx):
        ''' infer network prediction

        Parameters:
        -----------
        xx: tensor, graph input

        Returns:
        -------
        pred: tensor, prediction
        '''
        if self.feat_type != '3d':
            x_image = tf.reshape(xx, [-1, self.mea_dim[0], self.mea_dim[1], self.inputs])
            with tf.variable_scope('conv1') as scope:
                W_conv1 = weight_variable([self.c1size[0], self.c1size[1], self.inputs, self.l1depth], "wconv1", 1)
                b_conv1 = bias_variable([self.l1depth], "wb1")
                h_conv1 = tf.nn.relu(conv2d(x_image, W_conv1) + b_conv1, name=scope.name)
            h_pool1 = max_pool_2d(h_conv1)
            spatial_feat_size = np.ceil(np.array(self.mea_dim) / 2.)
            with tf.variable_scope('conv2') as scope:
                W_conv2 = weight_variable([self.c2size[0], self.c2size[1], self.l1depth, self.l2depth], "wconv2", 1)
                b_conv2 = bias_variable([self.l2depth], "wb2")
                h_conv2 = tf.nn.relu(conv2d(h_pool1, W_conv2) + b_conv2, name=scope.name)
            h_pool2 = max_pool_2d(h_conv2)
            spatial_feat_size = np.array(np.ceil(spatial_feat_size / 2.), dtype=int)
            with tf.variable_scope('local3') as scope:
                W_fc1 = weight_variable([np.prod(spatial_feat_size) * self.l2depth, self.fully], "wfc1", 1)
                b_fc1 = bias_variable([self.fully], "wbfc1")
                h_pool2_flat = tf.reshape(h_pool2, [-1, np.prod(spatial_feat_size) * self.l2depth])
                h_fc1 = tf.nn.relu(tf.matmul(h_pool2_flat, W_fc1) + b_fc1, name=scope.name)
        else:
            x_image = tf.reshape(xx, [-1, self.mea_dim[0], self.mea_dim[1], self.inputs, 1])
            with tf.variable_scope('conv1') as scope:
                W_conv1 = weight_variable([self.c1size[0], self.c1size[1], self.ctsize, 1, self.l1depth], "wconv1", 1)
                b_conv1 = bias_variable([self.l1depth], "wb1")
                h_conv1 = tf.nn.relu(conv3d(x_image, W_conv1) + b_conv1, name=scope.name)
            h_pool1 = max_pool_3d(h_conv1)
            spatial_feat_size = np.ceil(np.array(self.mea_dim) / 2.)
            temp_feat_size = np.ceil(self.inputs / 2.)
            with tf.variable_scope('conv2') as scope:
                W_conv2 = weight_variable([self.c2size[0], self.c2size[1], self.ctsize, self.l1depth, self.l2depth],
                                          "wconv2", 1)
                b_conv2 = bias_variable([self.l2depth], "wb2")
                h_conv2 = tf.nn.relu(conv3d(h_pool1, W_conv2) + b_conv2, name=scope.name)
            h_pool2 = max_pool_3d(h_conv2)
            spatial_feat_size = np.array(np.ceil(spatial_feat_size / 2.), dtype=int)
            temp_feat_size = int(np.ceil(temp_feat_size / 2.))
            with tf.variable_scope('local3') as scope:
                W_fc1 = weight_variable([np.prod(spatial_feat_size) * temp_feat_size * self.l2depth, self.fully],
                                        "wfc1", 1)
                b_fc1 = bias_variable([self.fully], "wbfc1")
                h_pool2_flat = tf.reshape(h_pool2, [-1, np.prod(spatial_feat_size) * temp_feat_size * self.l2depth])
                h_fc1 = tf.nn.relu(tf.matmul(h_pool2_flat, W_fc1) + b_fc1, name=scope.name)

        with tf.variable_scope('output') as scope:
            self.keep_prob = tf.placeholder("float", name='keep_prob')
            h_fc1_drop = tf.nn.dropout(h_fc1, self.keep_prob)
            W_fc2 = weight_variable([self.fully, self.out], "wfc2", 1)
            b_fc2 = bias_variable([self.out], "wbfc2")

            # Use simple linear comnination for regrassion (no softmax)
            y_conv = tf.matmul(h_fc1_drop, W_fc2) + b_fc2

        return y_conv


if __name__ == '__main__':
    '''
        COMMAND-LINE 
        -mod classification model
        -val validation data
    '''

    if '-mod' in sys.argv:
        pos = sys.argv.index('-mod')
        model_path = sys.argv[pos + 1]
    if '-val' in sys.argv:
        pos = sys.argv.index('-val')
        spikes = sys.argv[pos + 1]
    else:
        spikes = None
    if len(sys.argv) == 1:
        print('Arguments: \n   -mod CNN model path\n   ' \
              '-val validation data path')
    elif '-mod' not in sys.argv:
        raise AttributeError('Classification and localization model is required')
    else:
        pr = Prediction(model_path=model_path, spike_folder=spikes)
