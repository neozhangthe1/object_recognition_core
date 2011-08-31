#!/usr/bin/env python
import ecto
import ecto_ros
import ecto_pcl
import ecto_pcl_ros
from ecto_opencv import highgui, cv_bp as opencv, calib, imgproc, features2d
import ecto_sensor_msgs
import json
from optparse import OptionParser
import os
import sys
import time
from ecto_object_recognition import tod, tod_detection
from object_recognition.tod.feature_descriptor import FeatureDescriptor

DISPLAY = True
DEBUG = False

########################################################################################################################

ImageSub = ecto_sensor_msgs.Subscriber_Image
CameraInfoSub = ecto_sensor_msgs.Subscriber_CameraInfo

class TodDetectionKinectReader(ecto.BlackBox):
    """
    Blackbox that reasd data from the Kinect, returns an image and a camera frame point cloud
    """
    def __init__(self, plasm):
        ecto.BlackBox.__init__(self, plasm)

        subs = dict(image=ImageSub(topic_name='/camera/rgb/image_color', queue_size=0),
                    depth=ImageSub(topic_name='/camera/depth_registered/image', queue_size=0),
                    depth_info=CameraInfoSub(topic_name='/camera/depth_registered/camera_info', queue_size=0),
                    image_info=CameraInfoSub(topic_name='/camera/rgb/camera_info', queue_size=0),
                 )

        self._sync = ecto_ros.Synchronizer('Synchronizator', subs=subs)
        self._camera_info = ecto_ros.CameraInfo2Cv('camera_info -> cv::Mat')
        self._im2mat_rgb = ecto_ros.Image2Mat(swap_rgb = True)
        self._im2mat_depth = ecto_ros.Image2Mat()
        self._depth_to_3d = calib.DepthTo3d(do_organized = True)

    def expose_inputs(self):
        return {}

    def expose_outputs(self):
        return {'image': self._im2mat_rgb['image'],
                'points3d': self._depth_to_3d['points3d'],
                'K': self._camera_info['K']
                }

    def expose_parameters(self):
        return {}

    def connections(self):
        return (self._sync["image"] >> self._im2mat_rgb["image"],
                  self._sync["depth"] >> self._im2mat_depth["image"],
                  self._sync["image_info"] >> self._camera_info['camera_info'],
                  self._camera_info['K'] >> self._depth_to_3d['K'],
                  self._im2mat_depth['image'] >> self._depth_to_3d['depth']
                  )

########################################################################################################################

class TodDetectionBagReader(ecto.BlackBox):
    def __init__(self, plasm, baggers, bag):
        ecto.BlackBox.__init__(self, plasm)

        self._im2mat_rgb = ecto_ros.Image2Mat()
        self._camera_info_conversion = ecto_ros.CameraInfo2Cv()
        self._point_cloud_conversion = ecto_pcl_ros.Message2PointCloud(format=ecto_pcl.XYZRGB)
        self._point_cloud_conversion2 = ecto_pcl.PointCloud2PointCloudT(format=ecto_pcl.XYZRGB)
        self._bag_reader = ecto_ros.BagReader('Bag Reader',
                                baggers=baggers,
                                bag=options.bag,
                              )

    def expose_inputs(self):
        return {}

    def expose_outputs(self):
        return {'image': self._im2mat_rgb['image'],
                'point_cloud': self._point_cloud_conversion2['output']}

    def expose_parameters(self):
        return {}

    def connections(self):
        return (self._bag_reader['image'] >> self._im2mat_rgb['image'],
                  self._bag_reader['camera_info'] >> self._camera_info_conversion['camera_info'],
                  self._bag_reader['point_cloud'] >> self._point_cloud_conversion['input'],
                  self._point_cloud_conversion['output'] >> self._point_cloud_conversion2['input'])

########################################################################################################################

class TodDetector(ecto.BlackBox):
    def __init__(self, plasm, feature_descriptor_params, db_json_params, object_ids, search_json_params,
                 guess_json_params):
        ecto.BlackBox.__init__(self, plasm)

        self._db_json_params = db_json_params
        self._object_ids = object_ids
        self._guess_json_params = guess_json_params

        # parse the JSON and load the appropriate feature descriptor module
        self.feature_descriptor = FeatureDescriptor(feature_descriptor_params)
        self.descriptor_matcher = tod_detection.DescriptorMatcher(db_json_params=db_json_params, object_ids=object_ids,
                                                        search_json_params=search_json_params)
        self.guess_generator = tod_detection.GuessGenerator(json_params=guess_json_params)

    def expose_inputs(self):
        return {'image':self.feature_descriptor['image'],
                'mask':self.feature_descriptor['mask'],
                'points3d':self.guess_generator['points3d']}

    def expose_outputs(self):
        return {'object_ids': self.guess_generator['object_ids'],
                'Rs': self.guess_generator['Rs'],
                'Ts': self.guess_generator['Ts'],
                'keypoints': self.feature_descriptor['keypoints']}

    def expose_parameters(self):
        return {'db_json_params': self._db_json_params,
                'guess_json_params': self._guess_json_params,
                'object_ids': self._object_ids
                }

    def connections(self):
        return (self.feature_descriptor['keypoints'] >> self.guess_generator['keypoints'],
                self.feature_descriptor['descriptors'] >> self.descriptor_matcher['descriptors'],
                self.descriptor_matcher['matches'] >> self.guess_generator['matches'],
                self.descriptor_matcher['matches_3d'] >> self.guess_generator['matches_3d'])

########################################################################################################################

def parse_options():
    parser = OptionParser()
    parser.add_option("-b", "--bag", dest="bag", help="The bag to analyze")
    parser.add_option("-c", "--config_file", dest="config_file",
                      help='the file containing the configuration as JSON. It should contain the following fields.\n'
                      '"feature_descriptor": with parameters for "combination", "feature" and "descriptor".\n'
                      '"db": parameters about the db: "type", "url".\n'
                      '"objects_ids": the list of object to process, e.g. ["amys_country_cheddar_bowl",'
                      '"band_aid_plastic_strips"]\n'
                      '"search": the "type" of the search structure, the "radius" and/or "ratio" for the ratio test.\n'
                      )
    parser.add_option("-k", "--kinect", dest="do_kinect", help="if set to something, it will read data from the kinect")

    (options, args) = parser.parse_args()
    return options

########################################################################################################################


if __name__ == '__main__':
    options = parse_options()

    # define the input
    if options.config_file is None or not os.path.exists(options.config_file):
        raise 'option file does not exist'

    # Get the parameters from the file
    json_params = json.loads(str(open(options.config_file).read()))
    feature_descriptor_params = eval(str(json_params['feature_descriptor']).replace("'", '"').replace('u"', '"').\
                                     replace('{u', '{'))
    db_json_params = str(json_params['db']).replace("'", '"').replace('u"', '"').replace('{u', '{')
    object_ids = eval(str(json_params['object_ids']).replace("'", '"').replace('u"', '"').replace('{u', '{'))
    guess_json_params = str(json_params['guess']).replace("'", '"').replace('u"', '"').replace('{u', '{')
    search_json_params = str(json_params['search']).replace("'", '"').replace('u"', '"').replace('{u', '{')

    # define the input
    plasm = ecto.Plasm()
    tod_detector = TodDetector(plasm, feature_descriptor_params, db_json_params, object_ids, search_json_params,
                                 guess_json_params)

    if options.bag:
        bag_reader = TodDetectionBagReader(plasm, dict(image=ecto_sensor_msgs.Bagger_Image(topic_name='image_mono'),
                           camera_info=ecto_sensor_msgs.Bagger_CameraInfo(topic_name='camera_info'),
                           point_cloud=ecto_sensor_msgs.Bagger_PointCloud2(topic_name='points'),
                           ), options.bag)

        # connect to the model computation
        point_cloud_to_mat = tod_detection.PointCloudToMat()
        plasm.connect(bag_reader['image'] >> tod_detector['image'],
                      bag_reader['point_cloud'] >> point_cloud_to_mat['point_cloud'],
                      point_cloud_to_mat['points'] >> tod_detector['points'])

    elif options.do_kinect:
        ecto_ros.init(sys.argv, "ecto_node")
        kinect_reader = TodDetectionKinectReader(plasm)
        plasm.connect(kinect_reader['image'] >> tod_detector['image'],
                      kinect_reader['points3d'] >> tod_detector['points3d'])

    # write data back to a file
    guess_writer = tod_detection.GuessCsvWriter()
    plasm.connect(tod_detector['object_ids'] >> guess_writer['object_ids'],
                  tod_detector['Rs'] >> guess_writer['Rs'],
                  tod_detector['Ts'] >> guess_writer['Ts'],
                  )

    if DISPLAY:
        image_view = highgui.imshow(name="RGB", waitKey=1000, autoSize=True)
        keypoints_view = highgui.imshow(name="Keypoints", waitKey=1000, autoSize=True)
        pose_view = highgui.imshow(name="Pose", waitKey=1000, autoSize=True)
        draw_keypoints = features2d.DrawKeypoints()
        pose_drawer = calib.PosesDrawer()
        if options.do_kinect:
            plasm.connect(kinect_reader['image'] >> image_view['input'],
                       kinect_reader['image'] >> draw_keypoints['image'],
                       tod_detector['keypoints'] >> draw_keypoints['keypoints'],
                       draw_keypoints['image'] >> keypoints_view['input']
                       )
            # draw the poses
            plasm.connect(kinect_reader['image'] >> pose_drawer['image'],
                          kinect_reader['K'] >> pose_drawer['K'],
                          tod_detector['Rs'] >> pose_drawer['Rs'],
                          tod_detector['Ts'] >> pose_drawer['Ts'],
                          pose_drawer['output'] >> pose_view['input']
                          )

    # display DEBUG data if needed
    if DEBUG:
        print plasm.viz()
        ecto.view_plasm(plasm)

    # execute the pipeline
    sched = ecto.schedulers.Singlethreaded(plasm)
    sched.execute()
