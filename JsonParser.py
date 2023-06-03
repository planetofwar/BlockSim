import json
import sys
from collections import Counter
import os

def ParseOutput():
    # Opening JSON file
    f = open('./output/report.json')
    
    # returns JSON object as 
    # a dictionary
    data = json.load(f)
    
    # Iterating through the json
    # listfor count, item in enumerate(grocery):
    original_stdout = sys.stdout
    data_list = data['ohio-1_chain']
    chain_list = data_list['chain_list']
    with open('output.txt', 'w') as f:
        sys.stdout = f # Change the standard output to the file we created.
        for block in chain_list:
            print(block)
        sys.stdout = original_stdout
    score = [] 
    with open('output.txt', 'r') as f:
        for line_count, line in enumerate(f):
            words = 0
            record = False
            miner = ""
            for c in line:
                if words == 5:
                    record = False
                if record:
                    miner = miner + c
                if c == " ":
                    words = words + 1
                if words == 4 :
                    if c == ":":
                        record = True
            if(line_count > 0):
                score.append(miner)
    print("total blocks mined:",line_count)
    res = Counter(score)
    print("blocks for each miner:",res)

    
    # Closing file
    f.close()
    os.remove('output.txt')



