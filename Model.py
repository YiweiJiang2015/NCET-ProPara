'''
 @Date  : 12/11/2019
 @Author: Zhihan Zhang
 @mail  : zhangzhihan@pku.edu.cn
 @homepage: ytyz1307zzh.github.io
'''

import torch
import torch.nn as nn
import json
import os
import time
import numpy as np
from typing import List, Dict
from Constants import *
from utils import *
from allennlp.modules.elmo import Elmo
from torchcrf import CRF


class NCETModel(nn.Module):

    def __init__(self, batch_size: int, embed_size: int, hidden_size: int, dropout: float, elmo_dir: str):

        super(NCETModel, self).__init__()
        self.batch_size = batch_size
        self.hidden_size = hidden_size
        self.embed_size = embed_size

        self.EmbeddingLayer = NCETEmbedding(batch_size = batch_size, embed_size = embed_size,
                                            elmo_dir = elmo_dir, dropout = dropout)
        self.TokenEncoder = nn.LSTM(input_size = embed_size, hidden_size = hidden_size,
                                    num_layers = 1, batch_first = True, bidirectional = True)
        self.Dropout = nn.Dropout(p = dropout)
        self.StateTracker = StateTracker(batch_size = batch_size, hidden_size = hidden_size,
                                         dropout = dropout)
        self.CRFLayer = CRF(NUM_STATES, batch_first = True)
        

    def forward(self, char_paragraph: torch.Tensor, entity_mask: torch.IntTensor, verb_mask: torch.IntTensor,
                loc_mask: torch.IntTensor, gold_loc_seq: torch.IntTensor, gold_state_seq: torch.IntTensor, is_train: bool):
        """
        Args:
            gold_loc_seq: size (batch, max_sents)
            gold_state_seq: size (batch, max_sents)
        """
        max_tokens = char_paragraph.size(1)
        embeddings = self.EmbeddingLayer(char_paragraph, verb_mask)  # (batch, max_tokens, embed_size)
        token_rep, _ = self.TokenEncoder(embeddings)  # (batch, max_tokens, 2*hidden_size)
        token_rep = self.Dropout(token_rep)
        assert token_rep.size() == (self.batch_size, max_tokens, 2 * self.hidden_size)

        # size (batch, max_sents, NUM_STATES)
        tag_logits = self.StateTracker(encoder_out = token_rep, entity_mask = entity_mask, verb_mask = verb_mask)
        tag_mask = (gold_state_seq != PAD_STATE) # mask the padded part so they won't count in loss
        log_likelihood = self.CRFLayer(emissions = tag_logits, tags = gold_state_seq.long(), mask = tag_mask, reduction = 'mean')

        loss = -log_likelihood  # State classification loss is negative log likelihood
        pred_sequence = self.CRFLayer.decode(emissions = tag_logits, mask = tag_mask)
        assert len(pred_sequence) == self.batch_size
        accuracy = compute_tag_accuracy(pred = pred_sequence, gold = gold_state_seq.tolist(), pad_value = PAD_STATE)

        return loss, accuracy

    
class NCETEmbedding(nn.Module):

    def __init__(self, batch_size: int, embed_size: int, elmo_dir: str, dropout: float):

        super(NCETEmbedding, self).__init__()
        self.batch_size = batch_size
        self.embed_size = embed_size
        self.options_file = os.path.join(elmo_dir, 'elmo_2x4096_512_2048cnn_2xhighway_options.json')
        self.weight_file = os.path.join(elmo_dir, 'elmo_2x4096_512_2048cnn_2xhighway_weights.hdf5')
        self.elmo = Elmo(self.options_file, self.weight_file, num_output_representations=1, requires_grad=False,
                            do_layer_norm=False, dropout=0)
        self.embed_project = Linear(1024, self.embed_size - 1, dropout = dropout)  # 1024 is the default size of Elmo, leave 1 dim for verb indicator


    def forward(self, char_paragraph: torch.Tensor, verb_mask: torch.IntTensor):
        """
        Args: 
            char_paragraph - character ids of the paragraph, generated by function "batch_to_ids"
            verb_mask - size (batch, max_sents, max_tokens)
        Return:
            embeddings - token embeddings, size (batch, max_tokens, embed_size)
        """
        max_tokens = char_paragraph.size(1)
        elmo_embeddings = self.get_elmo(char_paragraph, max_tokens)
        elmo_embeddings = self.embed_project(elmo_embeddings)
        verb_indicator = self.get_verb_indicator(verb_mask, max_tokens)
        embeddings = torch.cat([elmo_embeddings, verb_indicator], dim = -1)

        assert embeddings.size() == (self.batch_size, max_tokens, self.embed_size)
        return embeddings


    def get_elmo(self, char_paragraph: torch.Tensor, max_tokens: int):
        """
        Compute the Elmo embedding of the paragraphs.
        Return:
            Elmo embeddings, size(batch, max_tokens, elmo_embed_size=1024)
        """
        # embeddings['elmo_representations'] is a list of tensors with length 'num_output_representations' (here it = 1)
        elmo_embeddings = self.elmo(char_paragraph)['elmo_representations'][0]  # (batch, max_tokens, elmo_embed_size=1024)
        assert elmo_embeddings.size() == (self.batch_size, max_tokens, 1024)
        return elmo_embeddings

    
    def get_verb_indicator(self, verb_mask, max_tokens: int):
        """
        Get the binary scalar indicator for each token
        """
        verb_indicator = torch.sum(verb_mask, dim = 1, dtype = torch.float).unsqueeze(dim = -1)
        assert verb_indicator.size() == (self.batch_size, max_tokens, 1)
        return verb_indicator


class Linear(nn.Module):
    ''' 
    Simple Linear layer with xavier init 
    '''
    def __init__(self, d_in: int, d_out: int, dropout: float, bias: bool = True):
        super(Linear, self).__init__()
        self.linear = nn.Linear(d_in, d_out, bias=bias)
        self.dropout = nn.Dropout(p = dropout)
        nn.init.xavier_normal_(self.linear.weight)

    def forward(self, x):
        return self.dropout(self.linear(x))


class StateTracker(nn.Module):
    """
    State tracking decoder: sentence-level Bi-LSTM + linear + CRF
    """
    def __init__(self, batch_size: int, hidden_size: int, dropout: float):

        super(StateTracker, self).__init__()
        self.batch_size = batch_size
        self.hidden_size = hidden_size
        self.Decoder = nn.LSTM(input_size = 4 * hidden_size, hidden_size = hidden_size,
                                    num_layers = 1, batch_first = True, bidirectional = True)
        self.Dropout = nn.Dropout(p = dropout)
        self.Hidden2Tag = Linear(d_in = 2 * hidden_size, d_out = NUM_STATES, dropout = 0)


    def forward(self, encoder_out, entity_mask, verb_mask):
        """
        Args:
            encoder_out: output of the encoder, size (batch, max_tokens, 2 * hidden_size)
            entity_mask: size (batch, max_sents, max_tokens)
            verb_mask: size (batch, max_sents, max_tokens)
        """
        max_sents = entity_mask.size(-2)
        decoder_in = self.get_masked_input(encoder_out, entity_mask, verb_mask)  # (batch, max_sents, 4 * hidden_size)
        decoder_out, _ = self.Decoder(decoder_in)  # (batch, max_sents, 2 * hidden_size), forward & backward concatenated
        decoder_out = self.Dropout(decoder_out)
        tag_logits = self.Hidden2Tag(decoder_out)  # (batch, max_sents, num_tags)
        assert tag_logits.size() == (self.batch_size, max_sents, NUM_STATES)

        return tag_logits
    

    def get_masked_input(self, encoder_out, entity_mask, verb_mask):
        """
        If the entity does not exist in this sentence (entity_mask is all-zero),
        then replace it with an all-zero vector;
        Otherwise, concat the average embeddings of entity and verb
        """
        assert entity_mask.size() == verb_mask.size()
        assert entity_mask.size(-1) == encoder_out.size(-2)

        max_sents = entity_mask.size(-2)
        entity_rep = self.get_masked_mean(source = encoder_out, mask = entity_mask)  # (batch, max_sents, 2 * hidden_size)
        verb_rep = self.get_masked_mean(source = encoder_out, mask = verb_mask)  # (batch, max_sents, 2 * hidden_size)
        concat_rep = torch.cat([entity_rep, verb_rep], dim = -1)  # (batch, max_sents, 4 * hidden_size)

        assert concat_rep.size() == (self.batch_size, max_sents, 4 * self.hidden_size)
        entity_existence = find_allzero_rows(vector = entity_mask).unsqueeze(dim = -1)  # (batch, max_sents, 1)
        masked_rep = concat_rep.masked_fill(mask = entity_existence, value = 0)
        assert masked_rep.size() == (self.batch_size, max_sents, 4 * self.hidden_size)

        return masked_rep


    def get_masked_mean(self, source, mask):
        """
        Args:
            source - input tensors, size(batch, tokens, 2 * hidden_size)
            mask - binary masked vectors, size(batch, sents, tokens)
        Return:
            the average of unmasked input tensors, size (batch, sents, 2 * hidden_size)
        """
        max_sents = mask.size(-2)

        bool_mask = (mask.unsqueeze(dim = -1) == 0)  # turn binary masks to boolean values
        masked_source = source.unsqueeze(dim = 1).masked_fill(bool_mask, value = 0)
        masked_source = torch.sum(masked_source, dim = -2)  # sum the unmasked vectors
        num_unmasked_tokens = torch.sum(mask, dim = -1, keepdim = True)  # compute the denominator of average op
        masked_mean = torch.div(input = masked_source, other = num_unmasked_tokens)  # average the unmasked vectors

        # division op may cause nan while encoutering 0, so replace nan with 0
        is_nan = torch.isnan(masked_mean)
        masked_mean = masked_mean.masked_fill(is_nan, value = 0)

        assert masked_mean.size() == (self.batch_size, max_sents, 2 * self.hidden_size)
        return masked_mean
