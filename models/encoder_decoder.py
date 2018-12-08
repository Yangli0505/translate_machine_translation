import torch
import torch.nn as nn
import math,copy
from torch import optim
import torch.nn.functional as F
from torch.autograd import Variable
import torch.nn.utils.rnn as rnn
from tools.Constants import *
import numpy as np

# check all the sizes!!!
# embd might need change for self attention, HarvardNLP multiply those weights by math.sqrt(self.emd_size)

## self-attention code adapted from https://github.com/harvardnlp/annotated-transformer/blob/master/The%20Annotated%20Transformer.ipynb

# Encoder architecture
# x -> embd -> multijhead attention -> layer norm -> feed forward -> layer norm -> sum_attn
# (sum_attn -> multijhead attention -> layer norm -> feed forward -> layer norm -> sum_attn )^N

def attention(query, key, value, mask=None, dropout=0.1):
    "Compute 'Scaled Dot Product Attention'"
    d_k = query.size(-1) # dim_emd_size // num_head
    scores = torch.matmul(query, key.transpose(-2, -1)) / math.sqrt(d_k)
             # (batch_size, target_len, d_k) * (batch_size, d_k, source_len)
    if mask is not None:
        scores = scores.masked_fill(mask == 0, -1e9)
    # after softmax, we can calculate hom much each word will be expressed at this position
    prob_attn = F.softmax(scores, dim = -1)
    if dropout is not None:
        prob_attn = dropout(prob_attn)
    sum_attn = torch.matmul(prob_attn, value)
    # sum is like the context vector, which will be sent to feed forward NN, and then sent to decoder
    return sum_attn#, prob_attn

def clones(module, N):
    return nn.ModuleList([copy.deepcopy(module) for _ in range(N)])

class MultiHeadedAttention(nn.Module):
    def __init__(self, num_head, emb_size, dropout=0.1):
        super(MultiHeadedAttention, self).__init__()
        self.emb_size = emb_size
        self.num_head = num_head
        self.d_k = emb_size // num_head
        # self.linears = clones(nn.Linear(emb_size, emb_size), 4)
        self.linear_Q = nn.Linear(emb_size, emb_size)
        self.linear_K = nn.Linear(emb_size, emb_size)
        self.linear_V = nn.Linear(emb_size, emb_size)
        self.linear = nn.Linear(emb_size, emb_size)
        self.attn = None
        self.dropout = nn.Dropout(dropout)

    def forward(self, query, key, value, mask=None):
        """
        @query: (batch_size, target_len, emb_size)
        @key: (batch_size, source_len, emb_size)
        @value: (batch_size, source_len, emb_size)
        @mask: mask future information
        """
        batch_size = query.size(0) 
#         batch_size, target_len, source_len = query.size(0), query.size(1), key.size(1)
        
        # do all the linear projections in batch from emb_size
        Q = self.linear_Q(query).view(batch_size, -1, self.num_head, self.d_k).transpose(1, 2)
        K = self.linear_K(key).view(batch_size, -1, self.num_head, self.d_k).transpose(1, 2)
        V = self.linear_V(value).view(batch_size, -1, self.num_head, self.d_k).transpose(1, 2)
        # Q = self.linear(query).view(batch_size*self.num_head, target_len, self.d_k).transpose(1, 2)
        # K = self.linear(key).view(batch_size*self.num_head, source_len, self.d_k).transpose(1, 2)
        # V = self.linear(value).view(batch_size*self.num_head, source_len, self.d_k).transpose(1, 2)

        # compute 'scaled dot product attention' 
        sum_attn = attention(query, key, value, mask, self.dropout)

        # concat
        sum_attn = sum_attn.transpose(1,2).contiguous().view(batch_size, -1, self.num_head * self.d_k)
        sum_attn = self.linear(sum_attn)

        return sum_attn

class FeedForwardSublayer(nn.Module):
    "Implements FFN equation."
    def __init__(self, emd_size, dim_ff, dropout=0.1):
        super(FeedForwardSublayer, self).__init__()
        self.linear1 = nn.Linear(emd_size, dim_ff)
        self.linear2 = nn.Linear(dim_ff, emd_size)
        self.dropout = nn.Dropout(dropout)

    def forward(self, sum_attn):
        
        out = self.linear1(sum_attn)
        out = F.relu(out)
        out = self.dropout(out)
        out = self.linear2(out)

        return out


class PositionalEncoding(nn.Module):
    "Implement the PE function."
    def __init__(self, emd_size, dropout = 0.1, max_len = 5000):
        super(PositionalEncoding, self).__init__()
        self.dropout = nn.Dropout(dropout)
        
        # Compute the positional encodings once in log space.
        pe = torch.zeros(max_len, emd_size)
        position = torch.arange(0., max_len).unsqueeze(1)
        div_term = torch.exp(torch.arange(0., emd_size, 2) * -(math.log(10000.0) / emd_size))
        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        pe = pe.unsqueeze(0)
        self.register_buffer('pe', pe)
        
    def forward(self, x):
        x = x + Variable(self.pe[:, :x.size(1)], 
                         requires_grad=False)
        return self.dropout(x)


class SelfAttentionEncoderLayer(nn.Module):
    def __init__(self, embd_size, self_attn, feed_forward, dropout = 0.1):
        
        super(SelfAttentionEncoderLayer, self).__init__()
        self.self_attn = self_attn          # MultiHeadedAttention
        self.feed_forward = feed_forward    # FeedForwardSublayer
        self.embd_size = embd_size
        self.layernorm1 = LayerNorm(embd_size)
        self.layernorm2 = LayerNorm(embd_size)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x, mask):

        # x is the source input
        residual = x

        x = self.self_attn(x, x, x, mask)
        x = residual + x
        x = self.layernorm1(x)
        x = self.dropout(x)

        residual = x
        x = x + residual
        x = self.feed_forward(x)
        x = residual + x
        x = self.layernorm2(x)

        return x

class SelfAttentionEncoder(nn.Module):
    "Core encoder is a stack of N layers"
    def __init__(self, layer, N):
        super(SelfAttentionEncoder, self).__init__()
        self.layers = clones(layer, N)
        self.norm = LayerNorm(layer.embd_size)
        
    def forward(self, x, mask):
        "Pass the input (and mask) through each layer in turn."
        for layer in self.layers:
            x = layer(x, mask)
        return self.norm(x)

class EncoderRNN_SelfAttn(nn.Module):
    def __init__(self, input_size, emb_dim, 
                 hidden_size, num_layers, 
                 decoder_layers, decoder_hidden_size,
                 pre_embedding, notPretrained,
                 use_bi=False, device=DEVICE, 
                 self_attn=False, attn_head=5):
        
        super(EncoderRNN_SelfAttn, self).__init__()
        self.hidden_size = hidden_size
        self.num_layers = num_layers
        self.use_bi = use_bi
        
        if pre_embedding is None:
            self.embedding_liquid = nn.Embedding(input_size, emb_dim, padding_idx=PAD)
            self.notPretrained = None
        elif notPretrained.all() == 1:
            self.embedding_liquid = nn.Embedding(input_size, emb_dim, padding_idx=PAD)
            self.embedding_liquid.weight = nn.Parameter(torch.FloatTensor(pre_embedding))
            self.notPretrained = None
        else:
            self.embedding_freeze = nn.Embedding(input_size, emb_dim, padding_idx=PAD)
            self.embedding_liquid = nn.Embedding(input_size, emb_dim, padding_idx=PAD)
            self.notPretrained = torch.FloatTensor(notPretrained[:, np.newaxis]).to(device)
            self.embedding_freeze.weight = nn.Parameter(torch.FloatTensor(pre_embedding))
            self.embedding_freeze.weight.requires_grad = False
        
        self.pe = PositionalEncoding(emb_dim)
        self.attn = MultiHeadedAttention(attn_head,emb_dim)
        self.ff = FeedForwardSublayer(emb_dim, hidden_size)
        self.layer=SelfAttentionEncoderLayer(emb_dim, self.attn, self.ff)
        self.encoder= SelfAttentionEncoder(self.layer,num_layers)
        self.decoder2h0 = nn.Sequential(nn.Linear(hidden_size, decoder_hidden_size*decoder_layers), nn.Tanh())
        self.output2=nn.Sequential(nn.Linear(hidden_size, 2*decoder_hidden_size), nn.Tanh())
        self.device = device
        
    def set_mask(self, encoder_input_lengths):
        seq_len = max(encoder_input_lengths).item()
        mask = (torch.arange(seq_len).expand(len(encoder_input_lengths), seq_len).to(self.device) < \
                encoder_input_lengths.unsqueeze(1)).to(self.device)
        return mask.detach()

    def forward(self, source, hidden, lengths):
        batch_size = source.size(0)
        seq_len = source.size(1)

        if self.notPretrained is None:
            embedded = self.embedding_liquid(source)
        else:
            embedded = self.embedding_freeze(source) # (batch_sz, seq_len, emb_dim)
            self.embedding_liquid.weight.data.mul_(self.notPretrained)
            embedded += self.embedding_liquid(source)
            
        embedded = self.pe(embedded)         
        mask = self.set_mask(lengths).unsqueeze(1)
        outputs=self.encoder(embedded, mask)
        hidden=outputs.mean(1).unsqueeze(1).transpose(0,1)
        hidden=self.decoder2h0(hidden)
        outputs=self.output2(outputs).view(batch_size, seq_len, 2, self.hidden_size)
        return None, hidden, outputs, torch.from_numpy(lengths.cpu().numpy())

    def initHidden(self, batch_size):
        return torch.zeros(self.num_layers*(1+self.use_bi), batch_size, self.hidden_size).to(self.device)

    
class SelfAttentionDecoderLayer(nn.Module):
    "Decoder is made of self-attn, src-attn, and feed forward (defined below)"
    def __init__(self, embd_size, self_attn, src_attn, feed_forward, dropout):
        
        super(SelfAttentionDecoderLayer, self).__init__()
        
        self.embd_size = embd_size 
        self.self_attn = self_attn # MultiHeadedAttention
        self.src_attn = src_attn   # MultiHeadedAttention
        self.ff1 = feed_forward
        self.ff2 = feed_forward
        self.layernorm1 = LayerNorm(embd_size)
        self.layernorm2 = LayerNorm(embd_size)
        self.layernorm3 = LayerNorm(embd_size)
        self.dropout1 = nn.Dropout(dropout)
        self.dropout2 = nn.Dropout(dropout)

 
    def forward(self, x, m, src_mask, tgt_mask):

        residual = x
        x = self.self_attn(query=x, key=x, value=x, tgt_mask)
        x = x + residual
        x = self.layernorm1(x)
        x = self.dropout1(x)

        residual = x
        x = x + residual
        x = self.src_attn(query=x, key=m, value=m, src_mask)
        x = x + residual
        x = self.layernorm2(x)
        x = self.dropout2(x)

        residual = x
        x = x + residual
        x = self.feed_forward(x)
        x = residual + x
        x = self.layernorm2(x)

        return x


class SelfAttentionDecoder(nn.Module):
    "Generic N layer decoder with masking."
    def __init__(self, layer, N):
        super(SelfAttentionDecoder, self).__init__()
        self.layers = clones(layer, N)
        self.norm = LayerNorm(layer.embd_size)
        
    def forward(self, x, memory, src_mask, tgt_mask):
        for layer in self.layers:
            x = layer(x, memory, src_mask, tgt_mask)
        return self.norm(x)
    
############################################################     
#               pending: Decoder_SelfAttn                  #
############################################################        
    
    
class EncoderRNN(nn.Module):
    def __init__(self, input_size, emb_dim, 
                 hidden_size, num_layers, 
                 decoder_layers, decoder_hidden_size,
                 pre_embedding, notPretrained,
                 use_bi=False, device=DEVICE, 
                 self_attn=False, attn_head=5):
        
        super(EncoderRNN, self).__init__()
        self.hidden_size = hidden_size
        self.decoder_layers = decoder_layers
        self.num_layers = num_layers
        self.use_bi = use_bi
        if pre_embedding is None:
            self.embedding_liquid = nn.Embedding(input_size, emb_dim, padding_idx=PAD)
            self.notPretrained = None
        elif notPretrained.all() == 1:
            self.embedding_liquid = nn.Embedding(input_size, emb_dim, padding_idx=PAD)
            self.embedding_liquid.weight = nn.Parameter(torch.FloatTensor(pre_embedding))
            self.notPretrained = None
        else:
            self.embedding_freeze = nn.Embedding(input_size, emb_dim, padding_idx=PAD)
            self.embedding_liquid = nn.Embedding(input_size, emb_dim, padding_idx=PAD)
            self.notPretrained = torch.FloatTensor(notPretrained[:, np.newaxis]).to(device)
            self.embedding_freeze.weight = nn.Parameter(torch.FloatTensor(pre_embedding))
            self.embedding_freeze.weight.requires_grad = False
        
        if self_attn:
            self.pe = PositionalEncoding(emb_dim)
            self.self_attn = MultiHeadedAttention(attn_head,emb_dim)
            self.self_attention = True
        else:
            self.self_attention = False

        self.gru = nn.GRU(emb_dim, hidden_size, num_layers=num_layers,
                          batch_first=True, bidirectional=use_bi, dropout=0.1)
        self.decoder2c = nn.Sequential(nn.Linear(hidden_size*(1+use_bi)*num_layers, hidden_size), nn.Tanh())
        self.decoder2h0 = nn.Sequential(nn.Linear(hidden_size, decoder_hidden_size*decoder_layers), nn.Tanh())

        self.device = device
        
    def set_mask(self, encoder_input_lengths):
        seq_len = max(encoder_input_lengths).item()
        mask = (torch.arange(seq_len).expand(len(encoder_input_lengths), seq_len).to(self.device) < \
                encoder_input_lengths.unsqueeze(1)).to(self.device)
        return mask.detach()

    def forward(self, source, hidden, lengths):
        batch_size = source.size(0)
        seq_len = source.size(1)

        if self.notPretrained is None:
            embedded = self.embedding_liquid(source)
        else:
            embedded = self.embedding_freeze(source) # (batch_sz, seq_len, emb_dim)
            self.embedding_liquid.weight.data.mul_(self.notPretrained)
            embedded += self.embedding_liquid(source)
            
        if self.self_attention: 
            embedded = self.pe(embedded)         
            mask = self.set_mask(lengths).unsqueeze(1)
            embedded = self.self_attn(embedded, embedded, embedded,mask)
            
        packed = rnn.pack_padded_sequence(embedded, lengths.cpu().numpy(), batch_first=True)
        outputs, hidden = self.gru(packed, hidden)
        outputs, output_lengths = rnn.pad_packed_sequence(outputs, batch_first=True)
        
        if self.use_bi:
            outputs = outputs.view(batch_size, seq_len, 2, self.hidden_size) # batch, seq_len, num_dir, hidden_sz
            hidden = outputs[:, 0, 1, :]
            hidden = self.decoder2h0(hidden)
            hidden = hidden.unsqueeze(0).contiguous().transpose(0, 1).view(
                batch_size, self.decoder_layers, -1).contiguous().transpose(0, 1).contiguous()
            return None, hidden, outputs, output_lengths
        else:
            hidden = hidden.transpose(0, 1).contiguous().view(batch_size, 1, -1).contiguous().transpose(0, 1)
            c = self.decoder2c(hidden) # (1, batch_sz, hidden_size)
            hidden = self.decoder2h0(c) # (1, batch_sz, decoder_hidden_size*decoder_layers)
            hidden = hidden.transpose(0, 1).view(batch_size, self.decoder_layers, -1).contiguous().transpose(0, 1)
            return c, hidden, outputs, output_lengths

    def initHidden(self, batch_size):
        return torch.zeros(self.num_layers*(1+self.use_bi), batch_size, self.hidden_size).to(self.device)


class DecoderRNN(nn.Module):
    def __init__(self, output_size, emb_dim, hidden_size, num_layers,
                 pre_embedding, notPretrained, dropout_p=0.1, device=DEVICE):
        super(DecoderRNN, self).__init__()

        # Define parameters
        self.output_size = output_size
        self.hidden_size = hidden_size
        self.output_size = output_size
        self.num_layers = num_layers
        self.dropout_p = dropout_p
        self.device = device

        # Define layers
        if pre_embedding is None:
            self.embedding_liquid = nn.Embedding(output_size, emb_dim, padding_idx=PAD)
            self.notPretrained = None
        elif notPretrained.all() == 1:
            self.embedding_liquid = nn.Embedding(output_size, emb_dim, padding_idx=PAD)
            self.embedding_liquid.weight = nn.Parameter(torch.FloatTensor(pre_embedding))
            self.notPretrained = None
        else:
            self.embedding_freeze = nn.Embedding(output_size, emb_dim, padding_idx=PAD)
            self.embedding_liquid = nn.Embedding(output_size, emb_dim, padding_idx=PAD)
            self.notPretrained = torch.FloatTensor(notPretrained[:, np.newaxis]).to(device)
            self.embedding_freeze.weight = nn.Parameter(torch.FloatTensor(pre_embedding))
            self.embedding_freeze.weight.requires_grad = False

        self.gru = nn.GRU(emb_dim+hidden_size, hidden_size, num_layers=num_layers, batch_first=True)
        self.maxout = Maxout(hidden_size + hidden_size + emb_dim, hidden_size, 2)
#         self.maxout = nn.Sequential(nn.Linear(hidden_size + hidden_size + emb_dim, hidden_size), nn.Tanh())
        self.linear = nn.Linear(hidden_size, output_size)

    def forward(self, word_input, last_hidden, c,
                encoder_outputs, encoder_output_lengths):
        """
        @ word_input: (batch, 1)
        @ last_hidden: (num_layers, batch, hidden_size)
        """
        if self.notPretrained is None:
            embedded = self.embedding_liquid(word_input)
        else:
            embedded = self.embedding_freeze(word_input) # (batch_sz, seq_len, emb_dim)
            self.embedding_liquid.weight.data.mul_(self.notPretrained)
            embedded += self.embedding_liquid(word_input)

        c = c.transpose(0, 1)

        rnn_input = torch.cat((embedded, c), dim=2)
        output, hidden = self.gru(rnn_input, last_hidden)

        output = output.squeeze(1) # B x hidden_size
        output = torch.cat((output, rnn_input.squeeze()), dim=1)
        output = self.maxout(output)
        output = self.linear(output)
        output = F.log_softmax(output, dim=1)

        return output, hidden, None


class DecoderRNN_Attention(nn.Module):
    def __init__(self, output_size, emb_dim, hidden_size, n_layers, pre_embedding, notPretrained,
                 dropout_p=0.1, device=DEVICE, method="dot"):
        super(DecoderRNN_Attention, self).__init__()

        self.hidden_size = hidden_size
        self.output_size = output_size
        self.n_layers = n_layers
        self.dropout_p = dropout_p
        self.device = device
        self.attn = Attention(hidden_size, method=method)

        if pre_embedding is None:
            self.embedding_liquid = nn.Embedding(output_size, emb_dim, padding_idx=PAD)
            self.notPretrained = None
        elif notPretrained.all() == 1:
            self.embedding_liquid = nn.Embedding(output_size, emb_dim, padding_idx=PAD)
            self.embedding_liquid.weight = nn.Parameter(torch.FloatTensor(pre_embedding))
            self.notPretrained = None
        else:
            self.embedding_freeze = nn.Embedding(output_size, emb_dim, padding_idx=PAD)
            self.embedding_liquid = nn.Embedding(output_size, emb_dim, padding_idx=PAD)
            self.notPretrained = torch.FloatTensor(notPretrained[:, np.newaxis]).to(device)
            self.embedding_freeze.weight = nn.Parameter(torch.FloatTensor(pre_embedding))
            self.embedding_freeze.weight.requires_grad = False

        self.dropout = nn.Dropout(dropout_p)
        self.gru = nn.GRU(self.hidden_size*self.n_layers + emb_dim, self.hidden_size,
                          self.n_layers, batch_first=True, dropout=self.dropout_p)
        self.maxout = Maxout(hidden_size + hidden_size*self.n_layers + emb_dim, hidden_size, 2)
        self.linear = nn.Linear(hidden_size, output_size)

    def forward(self, word_input, last_hidden, c,
                encoder_outputs, encoder_output_lengths):

        if self.notPretrained is None:
            embedded = self.embedding_liquid(word_input)
        else:
            embedded = self.embedding_freeze(word_input) # (batch_sz, seq_len, emb_dim)
            self.embedding_liquid.weight.data.mul_(self.notPretrained)
            embedded += self.embedding_liquid(word_input)
        
        attn_context, attn_weights = self.attn(encoder_outputs, last_hidden, encoder_output_lengths, self.device)

        rnn_input = torch.cat([attn_context, embedded], dim=2)
        output, hidden = self.gru(rnn_input, last_hidden)

        output = output.squeeze(1) # B x hidden_size
        output = torch.cat((output, rnn_input.squeeze()), dim=1)
        output = self.maxout(output)
        output = self.linear(output)
        output = F.log_softmax(output, dim=1)

        # Return final output, hidden state, and attention weights (for visualization)
        return output, hidden, attn_weights


class Attention(nn.Module):
    def __init__(self, hidden_size, method="cat"):
        super().__init__()
        self.hidden_size = hidden_size
        self.method = method
        self.preprocess = nn.Linear(hidden_size*2, hidden_size)
        self.energy = nn.Sequential(nn.Linear(hidden_size*2, hidden_size),
                                    nn.Tanh(),
                                    nn.Linear(hidden_size, 1))


    def set_mask(self, encoder_output_lengths, device):
        seq_len = max(encoder_output_lengths).item()
        mask = (torch.arange(seq_len).expand(len(encoder_output_lengths), seq_len) > \
                    encoder_output_lengths.unsqueeze(1)).to(device)
        return mask.detach()

    def forward(self, encoder_outputs, last_hidden, encoder_output_lengths, device):
        encoder_outputs = encoder_outputs.view(encoder_outputs.size(0), encoder_outputs.size(1), encoder_outputs.size(3)*2)
        if last_hidden.size(0) == 1:
            encoder_outputs = self.preprocess(encoder_outputs)
            dim_match = False # need to do transpose
        else:
            last_hidden = last_hidden.transpose(0, 1).contiguous().view(encoder_outputs.size(0), -1, 1) #(b, 1, 2*hidden)
            dim_match = True
            
        if self.method == "cat":
            if not dim_match:
                last_hidden = last_hidden.transpose(0, 1)
            last_hidden = last_hidden.expand_as(encoder_outputs)
            energy = self.energy(torch.cat([last_hidden.squeeze(), encoder_outputs], dim=2))
        elif self.method == "dot":
            if not dim_match:
                last_hidden = last_hidden.permute(1, 2, 0)
            energy = torch.bmm(encoder_outputs, last_hidden)
            # (batch_size, seq_len, 1)
        energy = energy.squeeze(2)
        mask = self.set_mask(encoder_output_lengths, device)

        energy.data.masked_fill_(mask, -float('inf'))
        attn = F.softmax(energy, dim=1).unsqueeze(1) # (batch_size, 1, seq_len)
        attn_context = torch.bmm(attn, encoder_outputs)
        # (batch_size, 1, seq_len) * (batch_size, seq_len, hidden_size)
        return attn_context, attn


class Maxout(nn.Module):

    def __init__(self, d_in, d_out, pool_size):
        super().__init__()
        self.d_in, self.d_out, self.pool_size = d_in, d_out, pool_size
        self.lin = nn.Linear(d_in, d_out * pool_size)


    def forward(self, inputs):
        shape = list(inputs.size())
        shape[-1] = self.d_out
        shape.append(self.pool_size)
        max_dim = len(shape) - 1
        out = self.lin(inputs)
        m, i = out.view(*shape).max(max_dim)
        return m

class LayerNorm(nn.Module):
    "Construct a layernorm module (See citation for details)."
    "Thanks to https://arxiv.org/abs/1607.06450 and http://nlp.seas.harvard.edu/2018/04/03/attention.html#model-architecture"
    
    def __init__(self, features, eps=1e-6):
        super(LayerNorm, self).__init__()
        self.a_2 = nn.Parameter(torch.ones(features))
        self.b_2 = nn.Parameter(torch.zeros(features))
        self.eps = eps

    def forward(self, x):
        mean = x.mean(-1, keepdim=True)
        std = x.std(-1, keepdim=True)
        return self.a_2 * (x - mean) / (std + self.eps) + self.b_2
