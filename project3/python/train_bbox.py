import cv2
import sys
import time
import os.path
import argparse
import util
import numpy as np

from settings import *
from sklearn import linear_model

def getBboxParameters(bboxes):
    c_x = 0.5 * (bboxes[:,0]+bboxes[:,2])
    c_y = 0.5 * (bboxes[:,1]+bboxes[:,3])
    H = bboxes[:,3] - bboxes[:,1] + 1
    W = bboxes[:,2] - bboxes[:,0] + 1

    return c_x, c_y, H, W

def trainBboxRegressionForClass(data, class_id):
    models = []
    X_train = []
    y_train = []

    num_images = len(data["train"]["gt"].keys())
    # Loop through all images and get features and bbox
    for i, image_name in enumerate(data["train"]["gt"].keys()):
        # if (i+1)%50 == 0:
        #     print 'Processing %d / %d'%(i+1, num_images)
        features_file_name = os.path.join(FEATURES_DIR, image_name + '.npy')
        if not os.path.isfile(features_file_name):
            print 'ERROR: Missing features file \'%s\''%(features_file_name) 
            sys.exit(1)

        features = np.load(features_file_name)

        if len(data["train"]["gt"][image_name][0]) == 0:
            # No Ground truth bboxes in image
            continue

        labels = np.array(data["train"]["gt"][image_name][0][0])
        gt_bboxes = np.array(data["train"]["gt"][image_name][1]).astype(np.int32) # Otherwise uint8 by default
        IDX = np.where(labels == class_id)[0]

        # Find positive examples by computing overlap of all regions with GT bbox
        regions = data["train"]["ssearch"][image_name]
        overlaps = np.zeros((len(IDX), regions.shape[0]))

        if len(IDX) == 0:
            # No Ground truth bboxes for this class in image
            continue

        gt_cx, gt_cy, gt_H, gt_W = getBboxParameters(gt_bboxes[IDX])

        for j, gt_bbox in enumerate(gt_bboxes[IDX]):
            overlaps = util.computeOverlap(gt_bbox, regions)
            positive_idx = np.where(overlaps > BBOX_POSITIVE_THRESHOLD)[0]

            gt_features = features[IDX[j], :]
            proposals_features = features[positive_idx + len(data["train"]["gt"][image_name][0][0]), :]
            proposals_bboxes = regions[positive_idx, :]

            proposals_cx, proposals_cy, proposals_H, proposals_W = getBboxParameters(proposals_bboxes)

            # Compute the bbox parameters: (center_x, center_y, log(width), log(height))
            X_train.append(gt_features)
            y_train.append(np.array([0.0, 0.0, 0.0, 0.0]))

            X_train.append(proposals_features)
            targets = np.zeros((proposals_features.shape[0], 4))

            # print proposals_cx.shape
            targets[:, 0] = np.divide((gt_cx[j] - proposals_cx), proposals_cx)
            targets[:, 1] = np.divide((gt_cy[j] - proposals_cy), proposals_cy)
            targets[:, 3] = np.log(gt_W[j]) - np.log(proposals_W)
            targets[:, 3] = np.log(gt_H[j]) - np.log(proposals_H)
            y_train.append(targets)

    X_train = util.stack(X_train)
    y_train = util.stack(y_train)

    print 'Num training examples:',X_train.shape[0]
    # Now train 4 different regressions, one for each bbox parameter (y_train)
    # So in total, for the three classes, we will have 12 regressions
    for i in xrange(y_train.shape[1]):
        model = linear_model.Ridge(alpha = 1000)
        model.fit(X_train, y_train[:,i])
        y_hat = model.predict(X_train)
        print 'Error (Model %d, Class %d): %0.2f'%(i+1, class_id, np.mean(np.abs(y_hat - y_train[:,i])))
        models.append(model)

    return models