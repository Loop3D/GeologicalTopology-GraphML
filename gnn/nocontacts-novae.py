from __future__ import division
from __future__ import print_function

import time
import os
import sys

# Train on CPU (hide GPU) due to memory constraints
os.environ['CUDA_VISIBLE_DEVICES'] = ""

import tensorflow as tf
import numpy as np
import scipy.sparse as sp

from sklearn.metrics import roc_auc_score
from sklearn.metrics import average_precision_score

from gae.optimizer import OptimizerAE, OptimizerVAE
from gae.input_data import load_data
from gae.model import GCNModelAE, GCNModelVAE
from gae.preprocessing import preprocess_graph, construct_feed_dict, sparse_to_tuple, mask_test_edges

# Extra things I need
import geopandas as gpd
import networkx as nx


def get_relationships(graph_file):
    
    # Read graph file into graph object
    G = nx.read_gml(graph_file, label='id')
    
    # Important for non-relational GCNs
    # G = make_undirected(G)
    G = remove_isolated_nodes(G)

    # Get nodes geological names instead of ids
    labels = nx.get_node_attributes(G, 'LabelGraphics')
    labels = [lab['text'] for lab in labels.values()]
    # Map old numeric labels to new descriptive ones to use as a figure legend
    old2name = dict(zip(G.nodes, labels))
    new_ids = list(range(len(G.nodes)))
    name2new = dict(zip(labels, new_ids))
    old2new = dict(zip(G.nodes, new_ids))
    
    # Replace node labels with new ids 
    H = nx.relabel_nodes(G, old2new)
    
#     # Format figure and add legend
#     legend_text = "\n".join(f"{v} - {k}" for k, v in old_name2num.items())
#     props = dict(boxstyle="round", facecolor="w", alpha=0.5)
#     ax.text(
#         1.15,
#         0.95,
#         legend_text,
#         transform=ax.transAxes,
#         fontsize=14,
#         verticalalignment="top",
#         bbox=props,
#     )
                    
    S = nx.to_pandas_adjacency(H).values
    return  H, S, old2name, old2new

def make_undirected(G):
    graph_dict = nx.to_dict_of_lists(G)

    for node in graph_dict.keys():
        neighbours = graph_dict[node]
        for n in neighbours:
            if node not in graph_dict[n]:
                G.add_edge(n, node)

    return G

def remove_isolated_nodes(G):
    graph_dict = nx.to_dict_of_lists(G)

    for node in graph_dict.keys():
        if len(graph_dict[node]) < 1:
            # Pick off nodes with no neighbours
            G.remove_node(node)
    
    return G

graph_file = "./wa-part/graph/graph_all_NONE.gml"
G, S, old2name, old2new = get_relationships(graph_file)

adj = nx.adjacency_matrix(G)

geol = gpd.read_file('./wa-part/tmp/geol_clip.shp')
geol_vec = geol[['code', 'unitname', 'formation', 'rocktype1', 'lithname1', 'min_age_ma', 'max_age_ma', 'supergroup']]

# for name in old2name.values():
#     print(name)
features_ = []
for unit_name in old2name.values():
    df = geol_vec[geol_vec['unitname'] == unit_name]
    modes = df.mode()

    min_age = 0
    max_age = 0
    try:
        min_age = float(modes['min_age_ma'].to_numpy()[0]) 
        max_age = float(modes['max_age_ma'].to_numpy()[0])
    except Exception as e:
        print("WARNING: There are missing age ranges in this area. Unitname -> ", unit_name)
        
    features_.append([min_age, max_age])
    
features = sp.lil_matrix(np.array(features_))



############################# Kipf and Welling (2016) implementation ############################

# Settings
flags = tf.app.flags
FLAGS = flags.FLAGS
flags.DEFINE_float('learning_rate', 0.01, 'Initial learning rate.')
flags.DEFINE_integer('epochs', 200, 'Number of epochs to train.')
flags.DEFINE_integer('hidden1', 32, 'Number of units in hidden layer 1.')
flags.DEFINE_integer('hidden2', 16, 'Number of units in hidden layer 2.')
flags.DEFINE_float('weight_decay', 0., 'Weight for L2 loss on embedding matrix.')
flags.DEFINE_float('dropout', 0., 'Dropout rate (1 - keep probability).')

flags.DEFINE_string('model', 'gcn_ae', 'Model string.')
flags.DEFINE_string('dataset', 'cora', 'Dataset string.')
flags.DEFINE_integer('features', 1, 'Whether to use features (1) or not (0).')

model_str = FLAGS.model
dataset_str = FLAGS.dataset

# Store original adjacency matrix (without diagonal entries) for later
adj_orig = adj
adj_orig = adj_orig - sp.dia_matrix((adj_orig.diagonal()[np.newaxis, :], [0]), shape=adj_orig.shape)
adj_orig.eliminate_zeros()

adj_train, train_edges, val_edges, val_edges_false, test_edges, test_edges_false = mask_test_edges(adj)
adj = adj_train

if FLAGS.features == 0:
    features = sp.identity(features.shape[0])  # featureless

# Some preprocessing
adj_norm = preprocess_graph(adj)

# Define placeholders
placeholders = {
    'features': tf.sparse_placeholder(tf.float32),
    'adj': tf.sparse_placeholder(tf.float32),
    'adj_orig': tf.sparse_placeholder(tf.float32),
    'dropout': tf.placeholder_with_default(0., shape=())
}

num_nodes = adj.shape[0]

features = sparse_to_tuple(features.tocoo())
num_features = features[2][1]
features_nonzero = features[1].shape[0]

# Create model
model = None
if model_str == 'gcn_ae':
    model = GCNModelAE(placeholders, num_features, features_nonzero)
elif model_str == 'gcn_vae':
    model = GCNModelVAE(placeholders, num_features, num_nodes, features_nonzero)

pos_weight = float(adj.shape[0] * adj.shape[0] - adj.sum()) / adj.sum()
norm = adj.shape[0] * adj.shape[0] / float((adj.shape[0] * adj.shape[0] - adj.sum()) * 2)

# Optimizer
with tf.name_scope('optimizer'):
    if model_str == 'gcn_ae':
        opt = OptimizerAE(preds=model.reconstructions,
                          labels=tf.reshape(tf.sparse_tensor_to_dense(placeholders['adj_orig'],
                                                                      validate_indices=False), [-1]),
                          pos_weight=pos_weight,
                          norm=norm)
    elif model_str == 'gcn_vae':
        opt = OptimizerVAE(preds=model.reconstructions,
                           labels=tf.reshape(tf.sparse_tensor_to_dense(placeholders['adj_orig'],
                                                                       validate_indices=False), [-1]),
                           model=model, num_nodes=num_nodes,
                           pos_weight=pos_weight,
                           norm=norm)

# Initialize session
sess = tf.Session()
sess.run(tf.global_variables_initializer())

cost_val = []
acc_val = []


def get_roc_score(edges_pos, edges_neg, emb=None):
    if emb is None:
        feed_dict.update({placeholders['dropout']: 0})
        emb = sess.run(model.z_mean, feed_dict=feed_dict)

    def sigmoid(x):
        return 1 / (1 + np.exp(-x))

    # Predict on test set of edges
    adj_rec = np.dot(emb, emb.T)
    preds = []
    pos = []
    for e in edges_pos:
        preds.append(sigmoid(adj_rec[e[0], e[1]]))
        pos.append(adj_orig[e[0], e[1]])

    preds_neg = []
    neg = []
    for e in edges_neg:
        preds_neg.append(sigmoid(adj_rec[e[0], e[1]]))
        neg.append(adj_orig[e[0], e[1]])

    preds_all = np.hstack([preds, preds_neg])
    labels_all = np.hstack([np.ones(len(preds)), np.zeros(len(preds_neg))])
    roc_score = roc_auc_score(labels_all, preds_all)
    ap_score = average_precision_score(labels_all, preds_all)

    return roc_score, ap_score


cost_val = []
acc_val = []
val_roc_score = []

adj_label = adj_train + sp.eye(adj_train.shape[0])
adj_label = sparse_to_tuple(adj_label)

# Train model
for epoch in range(FLAGS.epochs):

    t = time.time()
    # Construct feed dictionary
    feed_dict = construct_feed_dict(adj_norm, adj_label, features, placeholders)
    feed_dict.update({placeholders['dropout']: FLAGS.dropout})
    # Run single weight update
    outs = sess.run([opt.opt_op, opt.cost, opt.accuracy], feed_dict=feed_dict)

    # Compute average loss
    avg_cost = outs[1]
    avg_accuracy = outs[2]

    roc_curr, ap_curr = get_roc_score(val_edges, val_edges_false)
    val_roc_score.append(roc_curr)

    print("Epoch:", '%04d' % (epoch + 1), "train_loss=", "{:.5f}".format(avg_cost),
          "train_acc=", "{:.5f}".format(avg_accuracy), "val_roc=", "{:.5f}".format(val_roc_score[-1]),
          "val_ap=", "{:.5f}".format(ap_curr),
          "time=", "{:.5f}".format(time.time() - t))

print("Optimization Finished!")

roc_score, ap_score = get_roc_score(test_edges, test_edges_false)
print('Test ROC score: ' + str(roc_score))
print('Test AP score: ' + str(ap_score))