import tensorflow as tf
import os
import sys
from importlib import import_module
import numpy as np
import progressbar
import h5py
import re

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.abspath(os.path.join(BASE_DIR, os.pardir))
sys.path.append(BASE_DIR)
sys.path.append(os.path.join(BASE_DIR, 'utils'))
tf_util = import_module('tf_util')
file_pack = getattr(
    import_module('coder'),
    'file_pack'
)
iso_rect = getattr(
    import_module('iso_boxes'),
    'iso_rect'
)
iso_aabb = getattr(
    import_module('iso_boxes'),
    'iso_aabb'
)


class base_regre(object):
    """ This class holds baseline training approach using plain regression.
    """
    def __init__(self, out_dir):
        self.train_dir = os.path.join(out_dir, 'cropped')
        self.appen_train = 'appen_train'
        self.appen_test = 'appen_test'
        self.predict_file = 'predict_{}'.format(self.__class__.__name__)
        self.crop_size = 96
        self.batch_size = -1
        self.batchallot = None
        self.batch_bytes = 0
        self.train_list = []
        self.train_id = -1
        self.test_list = []
        self.test_id = -1
        self.pose_dim = 0
        self.image_dir = ''
        self.provider = None
        self.provider_worker = None

    class batch_allot:
        def __init__(self, store_size, image_size, pose_dim, batch_size=1):
            self.store_size = store_size
            self.batch_size = batch_size
            self.image_size = image_size
            self.pose_dim = pose_dim
            self.batch_beg = 0
            self.batch_end = self.batch_beg + self.batch_size

        def allot(self, num_channel, num_appen):
            self.batch_index = np.empty(
                shape=(self.batch_size, 1), dtype=np.int32)
            self.batch_frame = np.empty(
                shape=(self.batch_size, self.image_size, self.image_size, num_channel),
                dtype=np.float32)
            self.batch_poses = np.empty(
                shape=(self.batch_size, self.pose_dim), dtype=np.float32)
            self.batch_resce = np.empty(
                shape=(self.batch_size, num_appen), dtype=np.float32)
            self.batch_bytes = \
                self.batch_index.nbytes + self.batch_frame.nbytes + \
                self.batch_poses.nbytes + self.batch_resce.nbytes

        def assign(self, batch_index, batch_frame, batch_poses, batch_resce):
            self.batch_index = batch_index
            self.batch_frame = batch_frame
            self.batch_poses = batch_poses
            self.batch_resce = batch_resce
            self.batch_bytes = \
                self.batch_index.nbytes + self.batch_frame.nbytes + \
                self.batch_poses.nbytes + self.batch_resce.nbytes

        def fetch_batch(self):
            if self.batch_end >= self.store_size:
                return None
            batch_data = {
                'batch_index': self.batch_index[self.batch_beg:self.batch_end, ...],
                'batch_frame': self.batch_frame[self.batch_beg:self.batch_end, ...],
                'batch_poses': self.batch_poses[self.batch_beg:self.batch_end, ...],
                'batch_resce': self.batch_resce[self.batch_beg:self.batch_end, ...]
            }
            self.batch_beg = self.batch_end
            self.batch_end = self.batch_beg + self.batch_size
            return batch_data

    def read_next_file(self, filename):
        with h5py.File(filename, 'r') as h5file:
            store_size = h5file['index'][:].shape[0]
            # print(h5file['index'][:])
            # batch_index = h5file['index'][:]
            self.batchallot = self.batch_allot(
                store_size, self.crop_size, self.pose_dim, self.batch_size)
            self.batchallot.assign(
                h5file['index'][:],
                h5file['frame'][:],
                h5file['poses'][:],
                h5file['resce'][:]
            )

    def start_train(self, batch_size):
        self.batch_size = batch_size
        filelist = [f for f in os.listdir(self.train_dir)
                    if os.path.isfile(os.path.join(self.train_dir, f))]
        self.train_list = []
        for f in filelist:
            m = re.match(r'^(' + re.escape(self.appen_train) + r'_\d+)$', f)
            if m:
                self.train_list.append(os.path.join(self.train_dir, m.group(1)))
        self.test_list = []
        for f in filelist:
            m = re.match(r'^(' + re.escape(self.appen_test) + r'_\d+)$', f)
            if m:
                self.test_list.append(os.path.join(self.train_dir, m.group(1)))
        print('[{}] prepared for training'.format(
            self.__class__.__name__
        ))

    def start_epoch_train(self):
        self.read_next_file(self.train_list[0])
        self.train_id = 1

    def start_epoch_test(self):
        self.read_next_file(self.test_list[0])
        self.test_id = 1

    def fetch_batch_train(self):
        batch_data = self.batchallot.fetch_batch()
        if batch_data is None:
            if len(self.train_list) <= self.train_id:
                return None
            self.read_next_file(self.train_list[self.train_id])
            self.train_id += 1
            batch_data = self.batchallot.fetch_batch()
        return batch_data

    def fetch_batch_test(self):
        batch_data = self.batchallot.fetch_batch()
        if batch_data is None:
            if len(self.test_list) <= self.test_id:
                return None
            self.read_next_file(self.test_list[self.test_id])
            self.test_id += 1
            batch_data = self.batchallot.fetch_batch()
        return batch_data

    def tweak_args(self, args):
        """ Tweak algorithm specific parameters """
        args.crop_size = self.crop_size

    def prepare_data(self, thedata, batchallot, file_annot, name_appen):
        store_size = batchallot.store_size
        num_line = int(sum(1 for line in file_annot))
        file_annot.seek(0)
        num_stores = int(np.ceil(float(num_line) / store_size))
        print('[{}] preparing data: {:d} lines, subdivided into {:d} files \n\
              (producing {:.4f} GB for store size {:d}) ...'.format(
            self.__class__.__name__, num_line, num_stores,
            float(batchallot.batch_bytes) / (2 << 30), store_size
        ))
        timerbar = progressbar.ProgressBar(
            maxval=num_stores,
            widgets=[
                progressbar.Percentage(),
                ' ', progressbar.Bar('=', '[', ']'),
                ' ', progressbar.ETA()]
        ).start()
        bi = 0
        while True:
            resline = self.provider.puttensor_mt(
                file_annot, self.provider_worker,
                self.image_dir, thedata, batchallot
            )
            if 0 > resline:
                break
            filen_bi = '{}_{:d}'.format(name_appen, bi)
            with h5py.File(os.path.join(self.train_dir, filen_bi), 'w') as h5file:
                h5file.create_dataset(
                    'index', data=batchallot.batch_index[0:resline, ...], dtype=np.int32
                )
                h5file.create_dataset(
                    'frame', data=batchallot.batch_frame[0:resline, ...], dtype=np.float32
                )
                h5file.create_dataset(
                    'poses', data=batchallot.batch_poses[0:resline, ...], dtype=np.float32
                )
                h5file.create_dataset(
                    'resce', data=batchallot.batch_resce[0:resline, ...], dtype=np.float32
                )
            timerbar.update(bi)
            bi += 1
        timerbar.finish()

    def check_dir(self, thedata, args):
        first_run = False
        if not os.path.exists(self.train_dir):
            first_run = True
            os.makedirs(self.train_dir)
        if args.rebuild_data:
            first_run = True
        if not first_run:
            return
        batchallot = self.batch_allot(
            args.store_level, self.crop_size, self.pose_dim, args.store_level)
        batchallot.allot(1, 7)
        with file_pack() as filepack:
            file_annot = filepack.push_file(thedata.training_annot_train)
            self.prepare_data(thedata, batchallot, file_annot, self.appen_train)
        with file_pack() as filepack:
            file_annot = filepack.push_file(thedata.training_annot_test)
            self.prepare_data(thedata, batchallot, file_annot, self.appen_test)
        print('data prepared: {}'.format(self.train_dir))

    def receive_data(self, thedata, args):
        """ Receive parameters specific to the data """
        self.pose_dim = thedata.join_num * 3
        self.image_dir = thedata.training_images
        self.caminfo = thedata
        self.provider = args.data_provider
        self.provider_worker = self.provider.prow_cropped
        self.yanker = self.provider.yank_cropped
        self.check_dir(thedata, args)

    def draw_random(self, thedata, args):
        import matplotlib.pyplot as mpplot

        filelist = [f for f in os.listdir(self.train_dir)
                    if os.path.isfile(os.path.join(self.train_dir, f))]
        filename = os.path.join(self.train_dir, np.random.choice(filelist))
        with h5py.File(filename, 'r') as h5file:
            store_size = h5file['index'][:].shape[0]
            batchallot = self.batch_allot(
                store_size, self.crop_size, self.pose_dim, self.batch_size)
            batchallot.assign(
                h5file['index'][:],
                h5file['frame'][:],
                h5file['poses'][:],
                h5file['resce'][:]
            )
            frame_id = np.random.choice(store_size)
            img_id = batchallot.batch_index[frame_id, 0]
            frame_h5 = np.squeeze(batchallot.batch_frame[frame_id, ...], -1)
            poses_h5 = batchallot.batch_poses[frame_id, ...].reshape(-1, 3)
            resce_h5 = batchallot.batch_resce[frame_id, ...]

        print('[{}] drawing pose #{:d}'.format(self.__class__.__name__, img_id))
        # aabb = iso_aabb(resce_h5[2:5], resce_h5[1])
        # rect = args.data_ops.get_rect2(aabb, thedata)
        # resce2 = np.append(resce_h5[0], rect.cll)
        resce2 = resce_h5[0:3]
        resce3 = resce_h5[3:7]
        fig_size = (2 * 5, 2 * 5)
        mpplot.subplots(nrows=2, ncols=2, figsize=fig_size)

        mpplot.subplot(2, 2, 3)
        mpplot.imshow(frame_h5, cmap='bone')
        pose_raw = args.data_ops.local_to_raw(poses_h5, resce3)
        args.data_draw.draw_pose2d(
            thedata, frame_h5,
            args.data_ops.raw_to_2d(pose_raw, thedata, resce2)
        )

        mpplot.subplot(2, 2, 4)
        img_name = args.data_io.index2imagename(img_id)
        img = args.data_io.read_image(os.path.join(self.image_dir, img_name))
        mpplot.imshow(img, cmap='bone')
        pose_raw = self.yanker(poses_h5, resce_h5)
        args.data_draw.draw_pose2d(
            thedata, img,
            args.data_ops.raw_to_2d(pose_raw, thedata)
        )

        mpplot.subplot(2, 2, 1)
        annot_line = args.data_io.get_line(
            thedata.training_annot_cleaned, img_id)
        img_name, pose_raw = args.data_io.parse_line_annot(annot_line)
        img = args.data_io.read_image(os.path.join(self.image_dir, img_name))
        mpplot.imshow(img, cmap='bone')
        rect = iso_rect(resce_h5[1:3], self.crop_size / resce_h5[0])
        rect.draw()
        args.data_draw.draw_pose2d(
            thedata, img,
            args.data_ops.raw_to_2d(pose_raw, thedata))

        mpplot.subplot(2, 2, 2)
        img_name, frame, poses, resce = self.provider_worker(
            annot_line, self.image_dir, thedata)
        frame = np.squeeze(frame, axis=-1)
        poses = poses.reshape(-1, 3)
        if (
                # (1e-4 < np.linalg.norm(frame_h5 - frame)) or
                (1e-4 < np.linalg.norm(poses_h5 - poses))
        ):
            print(np.linalg.norm(frame_h5 - frame))
            print(np.linalg.norm(poses_h5 - poses))
            print('ERROR - h5 storage corrupted!')
        resce2 = resce[0:3]
        resce3 = resce[3:7]
        mpplot.imshow(frame, cmap='bone')
        pose_raw = args.data_ops.local_to_raw(poses, resce3)
        args.data_draw.draw_pose2d(
            thedata, frame,
            args.data_ops.raw_to_2d(pose_raw, thedata, resce2)
        )
        mpplot.savefig(os.path.join(
            args.data_inst.predict_dir,
            'draw_{}.png'.format(self.__class__.__name__)))
        mpplot.show()

    @staticmethod
    def placeholder_inputs(batch_size, image_size, pose_dim):
        frames_tf = tf.placeholder(
            tf.float32, shape=(batch_size, image_size, image_size, 1))
        poses_tf = tf.placeholder(
            tf.float32, shape=(batch_size, pose_dim))
        return frames_tf, poses_tf

    @staticmethod
    def get_model(frames_tf, pose_dim, is_training, bn_decay=None):
        """ directly predict all joints' location using regression
            frames_tf: BxHxWx1
            pose_dim: BxJ, where J is flattened 3D locations
        """
        batch_size = frames_tf.get_shape()[0].value
        end_points = {}
        # input_image = tf.expand_dims(frames_tf, -1)
        input_image = frames_tf

        net = tf_util.conv2d(
            input_image, 16, [5, 5],
            padding='VALID', stride=[1, 1],
            bn=True, is_training=is_training,
            scope='conv1', bn_decay=bn_decay)
        net = tf_util.max_pool2d(
            net, [4, 4],
            padding='VALID', scope='maxpool1')
        net = tf_util.conv2d(
            net, 32, [3, 3],
            padding='VALID', stride=[1, 1],
            bn=True, is_training=is_training,
            scope='conv2', bn_decay=bn_decay)
        net = tf_util.max_pool2d(
            net, [2, 2],
            padding='VALID', scope='maxpool2')
        net = tf_util.conv2d(
            net, 64, [3, 3],
            padding='VALID', stride=[1, 1],
            bn=True, is_training=is_training,
            scope='conv3', bn_decay=bn_decay)
        net = tf_util.max_pool2d(
            net, [2, 2],
            padding='VALID', scope='maxpool3')
        # print(net.shape)

        net = tf.reshape(net, [batch_size, -1])
        net = tf_util.fully_connected(
            net, 1024, bn=True, is_training=is_training,
            scope='fc1', bn_decay=bn_decay)
        net = tf_util.dropout(
            net, keep_prob=0.5, is_training=is_training,
            scope='dp1')
        net = tf_util.fully_connected(
            net, pose_dim, activation_fn=None, scope='fc3')

        return net, end_points

    @staticmethod
    def get_loss(pred, anno, end_points):
        """ simple sum-of-squares loss
            pred: BxJ
            anno: BxJ
        """
        # loss = tf.reduce_sum(tf.pow(tf.subtract(pred, anno), 2)) / 2
        # loss = tf.nn.l2_loss(pred - anno)  # already divided by 2
        loss = tf.reduce_mean(tf.squared_difference(pred, anno)) / 2
        return loss
