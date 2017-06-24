# -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function

import numpy as np
import tensorflow as tf

from utilities.misc_common import look_up_operations
from . import layer_util
from .activation import ActiLayer
from .base_layer import TrainableLayer
from .bn import BNLayer

SUPPORTED_PADDING = {'SAME', 'VALID'}


def default_w_initializer():
    def _initializer(shape, dtype, partition_info):
        stddev = np.sqrt(2.0 / np.prod(shape[:-1]))
        from tensorflow.python.ops import random_ops
        return random_ops.truncated_normal(shape, 0.0, stddev, dtype=tf.float32)
        # return tf.truncated_normal_initializer(
        #    mean=0.0, stddev=stddev, dtype=tf.float32)

    return _initializer


def default_b_initializer():
    return tf.constant_initializer(0.0)


class ConvTransLayer(TrainableLayer):
    """
    This class defines a simple convolution with an optional bias term.
    Please consider `ConvolutionalLayer` if batch_norm and activation
    are also used.
    """

    def __init__(self,
                 n_output_chns,
                 kernel_size=3,
                 stride=1,
                 padding='SAME',
                 with_bias=False,
                 w_initializer=None,
                 w_regularizer=None,
                 b_initializer=None,
                 b_regularizer=None,
                 name='conv'):
        super(ConvTransLayer, self).__init__(name=name)

        self.padding = look_up_operations(padding.upper(), SUPPORTED_PADDING)
        self.n_output_chns = n_output_chns
        self.kernel_size = np.asarray(kernel_size).flatten()
        self.stride = np.asarray(stride).flatten()
        self.with_bias = with_bias

        self.initializers = {
            'w': w_initializer if w_initializer else default_w_initializer(),
            'b': b_initializer if b_initializer else default_b_initializer()}

        self.regularizers = {'w': w_regularizer, 'b': b_regularizer}

    def layer_op(self, input_tensor):
        input_shape = input_tensor.get_shape().as_list()
        n_input_chns = input_shape[-1]
        spatial_rank = layer_util.infer_spatial_rank(input_tensor)

        # initialize conv kernels/strides and then apply
        w_full_size = np.vstack((
            [self.kernel_size] * spatial_rank,
            self.n_output_chns, n_input_chns)).flatten()
        full_stride = np.vstack((
            [self.stride] * spatial_rank)).flatten()
        conv_kernel = tf.get_variable(
            'w', shape=w_full_size.tolist(),
            initializer=self.initializers['w'],
            regularizer=self.regularizers['w'])
        output_shape = input_shape
        output_shape[-1] = self.n_output_chns
        # output_shape[1] *= 2
        output_tensor = tf.nn.conv3d_transpose(value=input_tensor,
                                          filter=conv_kernel,
                                          output_shape=output_shape,
                                          strides=[1]+full_stride.tolist()+[1],
                                          padding=self.padding,
                                          name='conv_trans')
        if not self.with_bias:
            return output_tensor

        # adding the bias term
        bias_term = tf.get_variable(
            'b', shape=self.n_output_chns,
            initializer=self.initializers['b'],
            regularizer=self.regularizers['b'])
        output_tensor = tf.nn.bias_add(output_tensor, bias_term,
                                       name='add_bias')
        return output_tensor


class ConvolutionalTransposeLayer(TrainableLayer):
    """
    This class defines a composite layer with optional components:
        transpose convolution -> batch_norm -> activation -> dropout
    The b_initializer and b_regularizer are applied to the ConvTransLayer
    The w_initializer and w_regularizer are applied to the ConvTransLayer,
    the batch normalisation layer, and the activation layer (for 'prelu')
    """

    def __init__(self,
                 n_output_chns,
                 kernel_size,
                 stride=1,
                 padding='SAME',
                 with_bias=False,
                 with_bn=True,
                 acti_func=None,
                 w_initializer=None,
                 w_regularizer=None,
                 b_initializer=None,
                 b_regularizer=None,
                 moving_decay=0.9,
                 eps=1e-5,
                 name="conv_trans"):

        self.acti_func = acti_func
        self.with_bn = with_bn
        self.layer_name = '{}'.format(name)
        if self.with_bn:
            self.layer_name += '_bn'
        if self.acti_func is not None:
            self.layer_name += '_{}'.format(self.acti_func)
        super(ConvolutionalTransposeLayer, self).__init__(name=self.layer_name)

        # for ConvTransLayer
        self.n_output_chns = n_output_chns
        self.kernel_size = kernel_size
        self.stride = stride
        self.padding = padding
        self.with_bias = with_bias

        # for BNLayer
        self.moving_decay = moving_decay
        self.eps = eps

        self.initializers = {
            'w': w_initializer if w_initializer else default_w_initializer(),
            'b': b_initializer if b_initializer else default_b_initializer()}

        self.regularizers = {'w': w_regularizer, 'b': b_regularizer}

    def layer_op(self, input_tensor, is_training=None, keep_prob=None):
        conv_trans_layer = ConvTransLayer(n_output_chns=self.n_output_chns,
                               kernel_size=self.kernel_size,
                               stride=self.stride,
                               padding=self.padding,
                               with_bias=self.with_bias,
                               w_initializer=self.initializers['w'],
                               w_regularizer=self.regularizers['w'],
                               b_initializer=self.initializers['b'],
                               b_regularizer=self.regularizers['b'],
                               name='conv_trans_')
        output_tensor = conv_trans_layer(input_tensor)

        if self.with_bn:
            bn_layer = BNLayer(
                regularizer=self.regularizers['w'],
                moving_decay=self.moving_decay,
                eps=self.eps,
                name='bn_')
            output_tensor = bn_layer(output_tensor, is_training)

        if self.acti_func is not None:
            acti_layer = ActiLayer(
                func=self.acti_func,
                regularizer=self.regularizers['w'],
                name='acti_')
            output_tensor = acti_layer(output_tensor)

        if keep_prob is not None:
            dropout_layer = ActiLayer(func='dropout', name='dropout_')
            output_tensor = dropout_layer(output_tensor, keep_prob=keep_prob)

        return output_tensor
