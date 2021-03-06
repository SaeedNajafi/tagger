import sys
import codecs

reload(sys)
sys.setdefaultencoding('utf-8')

#Used for final POS and CCG evaluation on test sets.
def accuracy(ref_file, pred_file):
    #Top1 Accuracy
    ref_lines = codecs.open(ref_file, 'r', 'utf-8').readlines()
    pred_lines = codecs.open(pred_file, 'r', 'utf-8').readlines()

    if len(ref_lines)!=len(pred_lines):
        print "INFO: Wrong number of lines in reference and prediction files"
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

print str(accuracy(sys.argv[1], sys.argv[2]))
