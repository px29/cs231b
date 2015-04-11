from matplotlib.patches import Rectangle
from graph_tool.all import *
from gmm import GMM
import matplotlib.pyplot as plt
import numpy as np
import argparse
import third_party.pymaxflow.pymaxflow as pymaxflow

import time

# Global constants
gamma = 50
beta = 1e-5 # TODO: optimize beta Boykov and Jolly 2001

SOURCE = -1
SINK = -2


# get_args function
# Intializes the arguments parser and reads in the arguments from the command
# line.
# 
# Returns: args dict with all the arguments
def get_args():
    parser = argparse.ArgumentParser(
        description='Implementation of the GrabCut algorithm.')
    parser.add_argument('image_file', 
        nargs=1, help='Input image name along with its relative path')

    return parser.parse_args()

# load_image function
# Loads an image using matplotlib's built in image reader
# Note: Requires PIL (python imaging library) to be installed if the image is
# not a png
# 
# Returns: img matrix with the contents of the image
def load_image(img_name):
    print 'Reading %s...' % img_name
    return plt.imread(img_name)

# RectSelector class
# Enables prompting user to select a rectangular area on a given image
class RectSelector:
    def __init__(self, ax):
        self.button_pressed = False
        self.start_x = 0
        self.start_y = 0
        self.canvas = ax.figure.canvas
        self.ax = ax
        self.canvas.mpl_connect('button_press_event', self.on_press)
        self.canvas.mpl_connect('button_release_event', self.on_release)
        self.canvas.mpl_connect('motion_notify_event', self.on_move)
        self.rectangle = []
    
    # Handles the case when the mouse button is initially pressed
    def on_press(self,event):
        self.button_pressed = True

        # Save the initial coordinates
        self.start_x = event.xdata
        self.start_y = event.ydata
        selected_rectangle = Rectangle((self.start_x,self.start_y),
                        width=0,height=0, fill=False, linestyle='dashed')

        # Add new rectangle onto the canvas
        self.ax.add_patch(selected_rectangle)
        self.canvas.draw()

    # Handles the case when the mouse button is released
    def on_release(self,event):
        self.button_pressed = False

        # Check if release happened because of mouse moving out of bounds,
        # in which case we consider it to be an invalid selection
        if event.xdata == None or event.ydata == None:
            return
        x = event.xdata
        y = event.ydata

        width = x - self.start_x
        height = y - self.start_y
        selected_rectangle = Rectangle((self.start_x,self.start_y),
                    width,height, fill=False, linestyle='solid')

        # Remove old rectangle and add new one
        self.ax.patches = []
        self.ax.add_patch(selected_rectangle)
        self.canvas.draw()
        xs = sorted([self.start_x, x])
        ys = sorted([self.start_y, y])
        self.rectangle = [xs[0], ys[0], xs[1], ys[1]]

        # Unblock plt
        plt.close()

    def on_move(self,event):
        # Check if the mouse moved out of bounds,
        # in which case we do not care about its position
        if event.xdata == None or event.ydata == None:
            return

        # If the mouse button is pressed, we need to update current rectangular
        # selection
        if self.button_pressed:
            x = event.xdata
            y = event.ydata

            width = x - self.start_x
            height = y - self.start_y
            
            selected_rectangle = Rectangle((self.start_x,self.start_y),
                            width,height, fill=False, linestyle='dashed')

            # Remove old rectangle and add new one
            self.ax.patches = []
            self.ax.add_patch(selected_rectangle)
            self.canvas.draw()

def get_user_selection(img):
    if img.shape[2] != 3:
        print 'This image does not have all the RGB channels, you do not need to work on it.'
        return
    
    # Initialize rectangular selector
    fig, ax = plt.subplots()
    selector = RectSelector(ax)
    
    # Show the image on the screen
    ax.imshow(img)
    plt.show()

    # Control reaches here once the user has selected a rectangle, 
    # since plt.show() blocks.
    # Return the selected rectangle
    return selector.rectangle

def initialization(img, bbox, debug=False):
    xmin, ymin, xmax, ymax = bbox
    height, width, _ = img.shape
    alpha = np.zeros((height, width), dtype=np.int8)

    for h in xrange(height): # Rows
        for w in xrange(width): # Columns
            if (w > xmin) and (w < xmax) and (h > ymin) and (h < ymax):
                # Foreground
                alpha[h,w] = 1
            else:
                # Background
                alpha[h,w] = 0

    foreground_gmm = GMM(5)
    background_gmm = GMM(5)

    foreground_gmm.initialize_gmm(img[alpha==1,:])
    background_gmm.initialize_gmm(img[alpha==0,:])

    if debug:
        plt.imshow(alpha*265)
        plt.show()
        for i in xrange(alpha.shape[0]):
            for j in xrange(alpha.shape[1]):
                print alpha[i,j],
            print ''

    return alpha, foreground_gmm, background_gmm

# Currently creates a meaningless graph
def create_graph(img, neighbor_list):
    num_neighbors = 8

    num_nodes = img.shape[0]*img.shape[1] + 2
    num_edges = img.shape[0]*img.shape[1]*num_neighbors

    g = pymaxflow.PyGraph(num_nodes, num_edges)

    # Creating nodes
    g.add_node(num_nodes-2)

    return g

# alpha,k - specific values
def get_pi(alpha, k, gmms):
    return gmms[alpha].weights[k]

def get_cov_det(alpha, k, gmms):
    return gmms[alpha].gaussians[k].sigma_det

def get_mean(alpha, k, gmms):
    return gmms[alpha].gaussians[k].mean

def get_cov_inv(alpha, k, gmms):
    return gmms[alpha].gaussians[k].sigma_inv

# Its not log_prob but we are calling it that for convinience
def get_log_prob(alpha, k, gmms, z_pixel):
    term = (z_pixel - get_mean(alpha, k, gmms))
    return 0.5 * np.dot(np.dot(term.T, get_cov_inv(alpha, k, gmms)), term)

def get_energy(alpha, k, gmms, z, smoothness_matrix):
    # Compute U
    U = 0
    for h in xrange(z.shape[0]):
        for w in xrange(z.shape[1]):
            U += -np.log(get_pi(alpha[h,w], k[h,w], gmms)) \
                + 0.5 * np.log(get_cov_det(alpha[h,w], k[h,w], gmms)) \
                + get_log_prob(alpha[h,w], k[h,w], gmms, z[h,w,:])

    # Compute V
    V = 0
    for h in xrange(z.shape[0]):
        for w in xrange(z.shape[1]):
            # Loop through neighbors
            for (nh, nw) in smoothness_matrix[(h,w)].keys():
                if alpha[h,w] != alpha[nh,nw]:
                    V += smoothness_matrix[(h,w)][(nh, nw)]
    V = gamma * V

    return U + V

def get_unary_energy(alpha, k, gmms, z, pixel):
    h,w = pixel
    return -np.log(get_pi(alpha, k[h,w], gmms)) \
            + 0.5 * np.log(get_cov_det(alpha, k[h,w], gmms)) \
            + get_log_prob(alpha, k[h,w], gmms, z[h,w,:])

def get_pairwise_energy(alpha, pixel_1, pixel_2, smoothness_matrix):
    (h,w) = pixel_1
    (nh,nw) = pixel_2
    V = 0
    if alpha[h,w] != alpha[nh,nw]:
        # print 'Pairwise',(h,w), (nh,nw)
        V = smoothness_matrix[(h,w)][(nh, nw)]

    return gamma *V

def compute_beta(z, debug=False):
    accumulator = 0
    m = z.shape[0]
    n = z.shape[1]

    for h in xrange(m-1):
        if debug: print 'Computing row', h
        for w in xrange(n):
            accumulator += np.linalg.norm(z[h,w,:] - z[h+1,w,:])**2

    for h in xrange(m):
        if debug: print 'Computing row', h
        for w in xrange(n-1):
            accumulator += np.linalg.norm(z[h,w,:] - z[h,w+1,:])**2

    num_comparisons = float(2*(m*n) - m - n)

    beta = (2*(accumulator/num_comparisons))**-1

    return beta
            

def compute_smoothness(z, debug=False):
    EIGHT_NEIGHBORHOOD = [(-1,-1), (-1,0), (-1,1), (0,-1), (0,1), (1,-1), (1,0), (1,1)]
    FOUR_NEIGHBORHOOD = [(-1,0), (0,-1), (0,1), (1,0)]

    height, width, _ = z.shape
    global beta
    smoothness_matrix = dict()

    beta = compute_beta(z)
    print 'beta',beta

    for h in xrange(height):
        if debug:
            print 'Computing row',h
        for w in xrange(width):
            if (h,w) not in smoothness_matrix:
                smoothness_matrix[(h,w)] = dict()
            for hh,ww in EIGHT_NEIGHBORHOOD:
                nh, nw = h + hh, w + ww
                if nw < 0 or nw >= width:
                    continue
                if nh < 0 or nh >= height:
                    continue

                if (nh,nw) not in smoothness_matrix:
                    smoothness_matrix[(nh,nw)] = dict()

                if (h,w) in smoothness_matrix[(nh,nw)]:
                    continue

                smoothness_matrix[(h,w)][(nh, nw)] = \
                    np.exp(-1 * beta * np.linalg.norm(z[h,w,:] - z[nh,nw,:]))
                smoothness_matrix[(nh,nw)][(h,w)] = smoothness_matrix[(h,w)][(nh, nw)]

                if debug:
                    print (h,w),'->',(nh,nw),":",z[h,w,:], z[nh,nw,:], smoothness_matrix[(h,w)][(nh, nw)]

    return smoothness_matrix

def main():
    args = get_args()
    img = load_image(*args.image_file)
    
    bbox = get_user_selection(img)
    # bbox = [10, 10, img.shape[0]-10, img.shape[1]-10]

    print 'Initializing gmms'
    alpha, foreground_gmm, background_gmm = initialization(img, bbox)
    k = np.zeros((img.shape[0],img.shape[1]), dtype=int)

    print 'Computing smoothness matrix...'
    start_time = time.time()
    smoothness_matrix = compute_smoothness(img, debug=False)
    end_time = time.time()

    print 'Took %d seconds'%(end_time-start_time)


    global SOURCE
    global SINK
    
    FOREGROUND = 1
    BACKGROUND = 0
    print 'Starting EM'
    for iteration in xrange(1,101):
        start_time = time.time()
        # 1. Assigning GMM components to pixels
        for h in xrange(img.shape[0]):
            for w in xrange(img.shape[1]):
                if alpha[h,w] == 1:
                    k[h,w] = foreground_gmm.get_component(img[h,w,:])
                else:
                    k[h,w] = background_gmm.get_component(img[h,w,:])

        # COLORS = [[255,0,0],[0,255,0], [0,0,255], [255,255,0], [255,0,255]]
        # res = np.zeros(img.shape, dtype=np.uint8)
        # for h in xrange(img.shape[0]):
        #     for w in xrange(img.shape[1]):
        #         res[h,w,:] = COLORS[k[h,w]] 
        # plt.imshow(res)
        # plt.show()

        # 2. Learn GMM parameters
        foreground_assignments = -1*np.ones(k.shape)
        foreground_assignments[alpha==1] = k[alpha==1]

        background_assignments = -1*np.zeros(k.shape)
        background_assignments[alpha==0] = k[alpha==0]

        foreground_gmm.update_components(img, foreground_assignments)
        background_gmm.update_components(img, background_assignments)

        # 3. Estimate segmentation using min cut
        print get_energy(alpha, k, (background_gmm, foreground_gmm), img, smoothness_matrix)
        
        # Update weights
        # # TODO: move energy computation here and update edge weights
        print 'Creating image graph'
        graph = create_graph(img, smoothness_matrix)
        theta = (background_gmm, foreground_gmm)
        for h in xrange(img.shape[0]):
            for w in xrange(img.shape[1]):
                index = h*img.shape[1] + w
                # Source: Compute U for curr node
                w1 = get_unary_energy(1, k, theta, img, (h, w)) # Foregound
                w2 = get_unary_energy(0, k, theta, img, (h, w)) # Background

                # Sink: Compute U for curr node
                graph.add_tweights(index, w1, w2)

                # Compute pairwise edge weights
                for (nh, nw) in smoothness_matrix[(h,w)].keys():
                    neighbor_index = nh * img.shape[1] + nw
                    edge_weight = get_pairwise_energy(alpha, (h,w), (nh,nw), smoothness_matrix)
                    graph.add_edge(index, neighbor_index, edge_weight, edge_weight)
                    # print (h,w),'->',(nh,nw),':',edge_weights[edge_map[(h,w,nh,nw)]]

        # Graph has been created, run minCut

        print 'Performing minCut'
        graph.maxflow()
        partition = graph.what_segment_vectorized()

        end_time = time.time()

        print 'Iteration %d time:'%iteration, end_time - start_time

        if iteration % 1 == 0:
            print 'Drawing graph'
            result = np.reshape(partition, (img.shape[0], img.shape[1]))*255
            result = result.astype(dtype=np.uint8)
            result = np.dstack((result, result, result))
            plt.imshow(result)
            plt.show()


        # pos = graph.new_vertex_property("vector<double>")
        # for h in xrange(img.shape[0]):
        #     for w in xrange(img.shape[1]):
        #         index = h*img.shape[1] + w
        #         pos[graph.vertex(index)] = np.array([h,w,0])
                
        # # _, pos = triangulation([for img.shape(0)])
        # res.a = cap.a - res.a  # the actual flow
        # graph_draw(graph, pos=pos, edge_pen_width=prop_to_size(cap, mi=3, ma=10, power=1),
        #                 edge_text=res, vertex_fill_color=partition, vertex_text=graph.vertex_index,
        #                 vertex_font_size=18, edge_font_size=18, fmt="png", output="example-min-st-cut.pdf")
        

        # for edge in edge_map:
        #     (sh,sw,dh,dw) = edge
        #     edge_weights[edge_map[edge]] = 

# TODO:
# gt : clear namespace
# 4 neighbors
# Optimize node matrix creation with index computation while creating graph
# Optimize pairwise edge weight computation

if __name__ == '__main__':
    main()