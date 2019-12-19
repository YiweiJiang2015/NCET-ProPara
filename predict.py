'''
 @Date  : 12/19/2019
 @Author: Zhihan Zhang
 @mail  : zhangzhihan@pku.edu.cn
 @homepage: ytyz1307zzh.github.io
'''

from typing import Dict, List
from Constants import *


def write_output(output: Dict[Dict], dummy_filepath: str, output_filepath: str):
    """
    Reads the headers of prediction file from dummy_filepath and fill in the blanks with prediction.
    Prediction will be stored according to output_filepath.
    """
    dummy_file = open(dummy_filepath, 'r', encoding='utf-8')
    output_file = open(output_filepath, 'w', encoding='utf-8')

    while True:

        dummy_line = dummy_file.readline()
        if not dummy_line:  # encouter EOF
            break

        fields = dummy_line.strip().split('\t')  # para_id, sent_id, entity, state (initially, NONE)
        assert len(fields) == 4 and fields[-1] == 'NONE'

        para_id = int(fields[0])
        sent_id = int(fields[1])
        entity_name = fields[2]
        pred_instance = output[str(para_id) + '-' + entity_name]

        total_sents = pred_instance['total_sents']
        assert sent_id <= total_sents
        assert para_id == pred_instance['id'] and entity_name == pred_instance['entity']

        prediction = pred_instance['prediction'][sent_id - 1]  # sent_id begins from 1
        state, loc_before, loc_after = prediction
        fields[-1] = state
        fields.append(loc_before)
        fields.append(loc_after)
        assert len(fields) == 6

        output_file.write('\t'.join(fields))


def get_output(metadata: Dict, pred_state_seq: List[int], pred_loc_seq: List[int]) -> Dict:
    """
    Get the predicted output from generated sequences by the model.
    """
    para_id = metadata['para_id']
    entity_name = metadata['entity']
    loc_cand_list = metadata['loc_cand_list']
    total_sents = metadata['total_sents']

    pred_state_seq = [idx2state[idx] for idx in pred_state_seq]  # pred_state_seq outside the function won't be changed
    pred_loc_seq = [loc_cand_list[idx] for idx in pred_loc_seq]  # pred_loc_seq outside the function won't be changed

    pred_loc_seq = predict_consistent_loc(pred_state_seq = pred_state_seq, pred_loc_seq = pred_loc_seq)
    prediction = format_final_prediction(pred_state_seq = pred_state_seq, pred_loc_seq = pred_loc_seq)
    assert len(prediction) == total_sents

    result = {'id': para_id,
              'entity': entity_name,
              'total_sents': total_sents,
              'prediction': prediction
              }
    return result


def format_final_prediction(pred_state_seq: List[str], pred_loc_seq: List[str]) -> List:
    """
    Final format: (state, loc_before, location_after) for each timestep (each sentence)
    """
    assert len(pred_state_seq) + 1 == len(pred_loc_seq)
    num_sents = len(pred_state_seq)
    prediction = []
    tag2state = {'O_C': 'NONE', 'O_D': 'NONE', 'C': 'CREATE', 'E': 'NONE', 'M': 'MOVE', 'D': 'DESTROY'}

    for i in range(num_sents):
        state_tag = pred_state_seq[i]
        prediction.append( (tag2state[state_tag], pred_loc_seq[i], pred_loc_seq[i+1]) )

    return prediction


# TODO: if state1 == 'E', then state0 should be '?' or state0 should be the same with state1?
# TODO: if state == 'M' but predicted location is the same with before, should I predict '?' or ignore?
def predict_consistent_loc(pred_state_seq: List[str], pred_loc_seq: List[str]) -> List[str]:
    """
    1. Only keep the location predictions at state "C" or "M"
    2. For "O_C", "O_D", and "D", location should be "-"
    3. For "E", location should be the same with previous timestep
    4. For state0: if state1 is "E", "M" or "D", then state0 should be "?";
       if state1 is "O_C", "O_D" or "C", then state0 should be "-"
    """

    assert len(pred_state_seq) == len(pred_loc_seq)
    num_sents = len(pred_state_seq)
    consist_loc_seq = []

    for sent_i in range(num_sents):

        state = pred_state_seq[sent_i]
        location = pred_loc_seq[sent_i]

        if sent_i == 0:
            location_0 = predict_loc0(state1 = state)
            consist_loc_seq.append(location_0)

        if state in ['O_C', 'O_D', 'D']:
            cur_location = '-'
        elif state == 'E':
            cur_location = consist_loc_seq[sent_i]  # this is the previous location since we add a location_0
        elif state in ['C', 'M']:
            cur_location = location

        consist_loc_seq.append(cur_location)

    assert len(consist_loc_seq) == num_sents + 1
    return consist_loc_seq


def predict_loc0(state1: str) -> str:

    assert state1 in state2idx.keys()

    if state1 in ['E', 'M', 'D']:
        loc0 = '?'
    elif state1 in ['O_C', 'O_D', 'C']:
        loc0 = '-'

    return loc0

# metadata = {'para_id': 249, 'entity': 'rocks ; smaller pieces', 'total_sents': 7, 'total_loc_cands': 4,
#             'loc_cand_list': ['pressure', 'pressure air', 'air', 'river', 'flower', 'water']}
# pred_state_seq = [4, 2, 2, 3, 5, 1, 1]
# pred_loc_seq = [0, 1, 2, 2, 4, 5, 3]
# print(get_output(metadata, pred_state_seq, pred_loc_seq))