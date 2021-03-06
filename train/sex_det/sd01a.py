import os
import argparse
import numpy as np
from sklearn.utils import shuffle as sklearn_shuffle
import tensorflow as tf
from keras.models import Model
from keras.layers import Flatten, Dense, Input, merge
from keras.layers import Convolution3D, MaxPooling3D, BatchNormalization, Activation, AveragePooling3D
from keras.callbacks import ModelCheckpoint, EarlyStopping

from helper.preprocessing_3d import ImageDataGenerator


def get_data_files(root):
    for item in os.scandir(root):
        if item.is_file() and item.path.endswith('.npy'):
            yield item.path
        elif item.is_dir():
            yield from get_data_files(item.path)


def create_data_generators(train_dir, val_dir, image_shape, batch_size):
    train_datagen = ImageDataGenerator(
        rotation_range=30,
        width_shift_range=0.125,
        height_shift_range=0.125,
        depth_shift_range=0.125,
        zoom_range=0.125,
        horizontal_flip=True,
        vertical_flip=False,
        depth_flip=False
    )
    val_datagen = ImageDataGenerator()

    train_neg_files = list(get_data_files(os.path.join(train_dir, '0')))
    train_pos_files = list(get_data_files(os.path.join(train_dir, '1')))
    total = len(train_neg_files) + len(train_pos_files)
    X_train = np.zeros((total, *image_shape))
    for i, f in enumerate(train_neg_files + train_pos_files):
        volume = np.load(f)
        X_train[i, :, :, :, :] = volume
    y_train = np.hstack((np.zeros(len(train_neg_files)), np.ones(len(train_pos_files)))).astype(np.bool)
    X_train, y_train = sklearn_shuffle(X_train, y_train)

    val_neg_files = list(get_data_files(os.path.join(val_dir, '0')))
    val_pos_files = list(get_data_files(os.path.join(val_dir, '1')))
    total = len(val_neg_files) + len(val_pos_files)
    X_val = np.zeros((total, *image_shape))
    for i, f in enumerate(val_neg_files + val_pos_files):
        volume = np.load(f)
        X_val[i, :, :, :, :] = volume
    y_val = np.hstack((np.zeros(len(val_neg_files)), np.ones(len(val_pos_files)))).astype(np.bool)
    X_val, y_val = sklearn_shuffle(X_val, y_val)

    train_generator = train_datagen.flow(X_train, y_train, batch_size=batch_size)
    val_generator = val_datagen.flow(X_val, y_val, batch_size=batch_size)

    return train_generator, val_generator


def res_block(input_tensor, nb_filters=16, block=0, subsample_factor=1):
    subsample = (subsample_factor, subsample_factor, subsample_factor)

    x = BatchNormalization(axis=4)(input_tensor)
    x = Activation('relu')(x)
    x = Convolution3D(nb_filters, 3, 3, 3, subsample=subsample, border_mode='same')(x)
    x = BatchNormalization(axis=4)(x)
    x = Activation('relu')(x)
    x = Convolution3D(nb_filters, 3, 3, 3, subsample=(1, 1, 1), border_mode='same')(x)

    if subsample_factor > 1:
        shortcut = Convolution3D(nb_filters, 1, 1, 1, subsample=subsample, border_mode='same')(input_tensor)
    else:
        shortcut = input_tensor

    x = merge([x, shortcut], mode='sum')
    return x


def define_model(image_shape):
    img_input = Input(shape=image_shape)

    x = Convolution3D(16, 5, 5, 5, subsample=(1, 1, 1), border_mode='same')(img_input)

    x = res_block(x, nb_filters=16, block=0, subsample_factor=1)
    x = res_block(x, nb_filters=16, block=0, subsample_factor=1)
    x = res_block(x, nb_filters=16, block=0, subsample_factor=1)

    x = res_block(x, nb_filters=32, block=1, subsample_factor=2)
    x = res_block(x, nb_filters=32, block=1, subsample_factor=1)
    x = res_block(x, nb_filters=32, block=1, subsample_factor=1)

    x = res_block(x, nb_filters=64, block=2, subsample_factor=2)
    x = res_block(x, nb_filters=64, block=2, subsample_factor=1)
    x = res_block(x, nb_filters=64, block=2, subsample_factor=1)

    x = res_block(x, nb_filters=128, block=3, subsample_factor=2)
    x = res_block(x, nb_filters=128, block=3, subsample_factor=1)
    x = res_block(x, nb_filters=128, block=3, subsample_factor=1)

    x = BatchNormalization(axis=4)(x)
    x = Activation('relu')(x)

    x = AveragePooling3D(pool_size=(4, 4, 8))(x)
    x = Flatten()(x)
    x = Dense(1, activation='sigmoid', name='predictions')(x)

    model = Model(img_input, x)
    model.compile(optimizer='adam', loss='binary_crossentropy', metrics=['accuracy', 'precision', 'recall', 'fmeasure'])
    model.summary()
    return model


def train(model, train_generator, val_generator, weights_filepath, config):
    (batch_size, nb_epoch, samples_per_epoch, nb_val_samples) = config

    checkpointer = ModelCheckpoint(filepath=weights_filepath, verbose=2, save_best_only=True)
    early_stopping = EarlyStopping(monitor='val_loss', patience=10, verbose=2)

    model.fit_generator(train_generator, samples_per_epoch, nb_epoch,
                        verbose=2, callbacks=[checkpointer, early_stopping],
                        validation_data=val_generator, nb_val_samples=nb_val_samples,
                        class_weight=None, max_q_size=20, nb_worker=4,
                        pickle_safe=False, initial_epoch=0)


if __name__ == '__main__':
    SETTINGS_FILE_PATH = '../../SETTINGS.json'
    BASEPATH = os.path.dirname(os.path.abspath(SETTINGS_FILE_PATH))

    with open(SETTINGS_FILE_PATH, 'r') as f:
        SETTINGS = json.load(f)

    image_shape = (32, 32, 64, 1)

    train_dir = os.path.join(BASEPATH, 'data_train/stage1/sex_det/volumes_1/train')
    val_dir = os.path.join(BASEPATH, 'data_train/stage1/sex_det/volumes_1/val')
    weights_filepath = os.path.join(BASEPATH, 'weights/stage1/sex_det/{}'.format(
        os.path.basename(__file__).replace('.py', '.hdf5')
    ))
    os.makedirs(os.path.join(BASEPATH, 'weights/stage1/sex_det'), exist_ok=True)

    # training config
    batch_size = 16
    nb_epoch = 1000
    samples_per_epoch = batch_size * (len(list(get_data_files(train_dir))) // batch_size)
    nb_val_samples = batch_size * (len(list(get_data_files(val_dir))) // batch_size)
    training_config = (batch_size, nb_epoch, samples_per_epoch, nb_val_samples)

    print('training config\n-------------------')
    print('batch_size:', batch_size)
    print('samples_per_epoch:', samples_per_epoch)
    print('nb_epoch:', nb_epoch)
    print('nb_val_samples:', nb_val_samples)
    print('paths\n-------------------')
    print('train dir:', train_dir)
    print('val dir:', val_dir)
    print('weights filepath:', weights_filepath)
    print('# train(-):', len(list(get_data_files(train_dir + '/0'))))
    print('# train(+):', len(list(get_data_files(train_dir + '/1'))))
    print('# val(-):', len(list(get_data_files(val_dir + '/0'))))
    print('# val(+):', len(list(get_data_files(val_dir + '/1'))))
    print('\n')

    train_generator, val_generator = create_data_generators(train_dir, val_dir, image_shape, batch_size)
    model = define_model(image_shape)
    train(model, train_generator, val_generator, weights_filepath, training_config)
