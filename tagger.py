from config import Configuration
from load import load_embeddings
from load import load_data
from feature import Feature
from encoder import Encoder
from indp import INDP
import re
import os
import sys
import time
import numpy as np
import torch
import torch.optim as optim

#Global variables for models and the optimizers.
feature = None
encoder = None
indp = None
feature_optim = None
encoder_optim = None
indp_optim = None

def batch_to_tensors(cfg, in_B):
    o_B = {}
    o_B['ch'] = torch.LongTensor(in_B['ch'])
    o_B['rev_ch'] = torch.LongTensor(in_B['rev_ch'])
    o_B['w_len'] = torch.LongTensor(in_B['w_len'])
    o_B['w'] = torch.LongTensor(in_B['w'])
    o_B['w_chs'] = torch.LongTensor(in_B['w_chs'])
    o_B['w_cap'] = torch.LongTensor(in_B['w_cap'])
    o_B['w_mask'] = torch.FloatTensor(in_B['w_mask'])
    if in_B['tag'] is not None:
        o_B['tag'] = torch.LongTensor(in_B['tag'])
    else:
        o_B['tag'] = None

    if in_B['tag'] is not None:
        tag_one_hot = np.zeros((cfg.d_batch_size * cfg.max_s_len, cfg.tag_size))
        tag_one_hot[np.arange(cfg.d_batch_size * cfg.max_s_len), np.reshape(in_B['tag'], (-1,))] = 1.0
        tag_o_h = np.reshape(tag_one_hot, (cfg.d_batch_size, cfg.max_s_len, cfg.tag_size))
        o_B['tag_o_h'] = torch.FloatTensor(tag_o_h)
    else:
        o_B['tag_o_h'] = None

    return o_B

def run_epoch(cfg):
    cfg.local_mode = 'train'

    total_loss = []

    #Turn on training mode which enables dropout.
    feature.train()
    encoder.train()
    indp.train()

    for step, batch in enumerate(load_data(cfg)):
        feature.zero_grad()
        encoder.zero_grad()
        indp.zero_grad()
        B = batch_to_tensors(cfg, batch)
        F = feature(cfg, B)
        H = encoder(cfg, F, B)
        log_probs = indp(H)
        loss = indp.ML_loss(B, log_probs)
        loss.backward()
        torch.nn.utils.clip_grad_norm(indp.parameters(), cfg.max_gradient_norm)
        torch.nn.utils.clip_grad_norm(encoder.parameters(), cfg.max_gradient_norm)
        torch.nn.utils.clip_grad_norm(feature.parameters(), cfg.max_gradient_norm)
        feature_optim.step()
        encoder_optim.step()
        indp_optim.step()
        loss_value = loss.cpu().data.numpy()[0]
        total_loss.append(loss_value)
        ##
        sys.stdout.write('\rStep:{} | Loss:{} | Mean Loss:{}'.format(
                                                step,
                                                loss_value,
                                                np.mean(total_loss)
                                                )
                        )
        sys.stdout.flush()
    return

def predict(cfg, o_file):
    if cfg.mode=='train':
        cfg.local_mode = 'dev'

    elif cfg.mode=='test':
        cfg.local_mode = 'test'

    #Turn on evaluation mode which disables dropout.
    feature.eval()
    encoder.eval()
    indp.eval()

    #file stream to save predictions
    f = open(o_file, 'w')
    for batch in load_data(cfg):
        B = batch_to_tensors(cfg, batch)
        F = feature(cfg, B)
        H = encoder(cfg, F, B)
        log_probs = indp(H)
        if cfg.model_type=='INDP':
            preds = np.argmax(log_probs.cpu().data.numpy(), axis=2)

        save_predictions(cfg, batch, preds, f)

    f.close()
    return

def save_predictions(cfg, batch, preds, f):
    """Saves predictions to the provided file stream."""
    #Sentence index
    s_idx = 0
    for pred in preds:
        #Word index inside sentence
        w_idx = 0
        while(w_idx < batch['s_len'][s_idx]):
            #w is the word for which we predict a tag
            w = batch['w'][s_idx][w_idx]
            str_w = cfg.data['id_w'][w]

            #tag is the predicted tag for w
            tag = pred[w_idx]
            str_tag = cfg.data['id_tag'][tag]
            f.write(str_w + '\t' + str_tag + '\n')

            #Go to the next word in the sentence
            w_idx += 1

        #Go to the next sentence
        f.write("\n")
        s_idx += 1

    return

def eval_on_dev(cfg, pred_file):
    #accuracy
    ref_lines = open(cfg.dev_ref, 'r').readlines()
    pred_lines = open(pred_file, 'r').readlines()

    if len(ref_lines)!=len(pred_lines):
        print "INFO: Wrong number of lines in reference and prediction files for dev set."
        exit()

    total = 0.0
    correct = 0.0
    for index in range(len(ref_lines)):
        ref_line = ref_lines[index].strip()
        pred_line = pred_lines[index].strip()
        if len(ref_line)!=0 and len(pred_line)!=0:
            Gtags = ref_line.split('\t')
            tag = pred_line.split('\t')[1]
            total += 1
            for gtag in Gtags:
                if gtag==tag:
                    correct += 1
                    break

    return float(correct/total) * 100

def run_model(mode, path, in_file, o_file):
    global feature, encoder, indp, feature_optim, encoder_optim, indp_optim

    cfg = Configuration()
    #General mode has two values: 'train' or 'test'
    cfg.mode = mode

    #Set Random Seeds
    np.random.seed(cfg.seed)
    torch.manual_seed(cfg.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(cfg.seed)

    #Load Embeddings
    load_embeddings(cfg)

    #Only for testing
    if mode=='test': cfg.test_raw = in_file

    #Construct models
    feature = Feature(cfg)
    encoder = Encoder(cfg)
    indp = INDP(cfg)
    feature_optim = optim.Adam(feature.parameters(), lr=cfg.learning_rate)
    encoder_optim = optim.Adam(encoder.parameters(), lr=cfg.learning_rate)
    indp_optim = optim.Adam(indp.parameters(), lr=cfg.learning_rate)

    #Move models to cuda if possible
    if torch.cuda.is_available():
        feature.cuda()
        encoder.cuda()
        indp.cuda()

    if mode=='train':
        o_file = './temp.predicted'
        best_val_cost = float('inf')
        best_val_epoch = 0
        first_start = time.time()
        epoch=0
        while (epoch < cfg.max_epochs):
            print
            print 'Model:{} | Epoch:{}'.format(cfg.model_type, epoch)
            start = time.time()
            run_epoch(cfg)
            print '\nModel:{} Validation'.format(cfg.model_type, epoch)
            predict(cfg, o_file)
            val_cost = 100 - eval_on_dev(cfg, o_file)
            print '\nValidation score:{}'.format(100 - val_cost)
            if val_cost < best_val_cost:
                best_val_cost = val_cost
                best_val_epoch = epoch
                torch.save(feature.state_dict(), path+'model_feature')
                torch.save(encoder.state_dict(), path+'model_encoder')
                torch.save(indp.state_dict(), path+'model_indp')

            #For early stopping
            if epoch - best_val_epoch > cfg.early_stopping:
                break
                ###

            print 'Epoch training time:{} seconds'.format(time.time() - start)
            epoch += 1

        print 'Total training time:{} seconds'.format(time.time() - first_start)

    elif mode=='test':
        feature.load_state_dict(torch.load(path+'model_feature'))
        encoder.load_state_dict(torch.load(path+'model_encoder'))
        indp.load_state_dict(torch.load(path+'model_indp'))
        print
        print 'Model:{} Predicting'.format(cfg.model_type)
        start = time.time()
        predict(cfg, o_file)
        print 'Total prediction time:{} seconds'.format(time.time() - start)
    return

"""
    For training: python tagger.py train <path to save model>
    example: python tagger.py train ./saved_models/

    For testing: python tagger.py test <path to restore model> <input file path> <output file path>
    example: python tagger.py test ./saved_models/ ./data/test.raw ./saved_models/test.predicted
    or: python tagger.py test ./saved_models/ ./data/dev.raw ./saved_models/dev.predicted
"""
if __name__ == "__main__":
    mode = sys.argv[1]
    path = sys.argv[2]
    in_file = None
    o_file = None
    if mode=='test':
        in_file = sys.argv[3]
        o_file = sys.argv[4]

    run_model(mode, path, in_file, o_file)
