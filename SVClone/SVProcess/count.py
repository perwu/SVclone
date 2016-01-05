'''
Using characterised SVs, count normal and supporting reads at SV locations
'''
import warnings
import os
import numpy as np
import pysam
import csv
import ipdb
from collections import OrderedDict
from operator import methodcaller
import vcf
from . import parameters as params
from . import bamtools
from . import svDetectFuncs as svd
from . import load_data

def read_to_array(x,bamf):
    chrom = bamf.getrname(x.reference_id)
    try:
        read = np.array((x.query_name,chrom,x.reference_start,x.reference_end,x.query_alignment_start,
                         x.query_alignment_end,x.query_length,x.tlen,np.bool(x.is_reverse)),dtype=params.read_dtype)
        return read
    except TypeError:
        print 'Warning: record %s contains invalid attributes, skipping' % x.query_name
        #return np.empty(len(params.read_dtype),dtype=params.read_dtype)
        return np.empty(0)

def is_soft_clipped(read):
    return read['align_start'] != 0 or (read['len'] + read['ref_start'] != read['ref_end'])

def is_below_sc_threshold(read,threshold=params.tr):
    return (read['align_start'] < threshold) and (read['len'] - read['align_end'] < threshold)

def is_normal_non_overlap(read,mate,pos,min_ins,max_ins):
    '''
    if read and mate have normal insert size, are not soft-clipped,
    and do not overlap the breakpoint (insert or read), return true
    '''
    if read['chrom']!=mate['chrom']:
        return False
    return  is_below_sc_threshold(read) and is_below_sc_threshold(mate) and \
            (abs(read['ins_len']) < max_ins and abs(read['ins_len']) > min_ins) and \
            (abs(mate['ins_len']) < max_ins and abs(mate['ins_len']) > min_ins) and \
            not (read['ref_start'] < (pos + params.tr) and read['ref_end'] > (pos - params.tr)) and \
            not (mate['ref_start'] < (pos + params.tr) and mate['ref_end'] > (pos - params.tr)) and \
            not (read['ref_start'] < pos and mate['ref_end'] > pos)

def is_normal_across_break(read,pos,min_ins,max_ins,sc_len):
    # must overhang break by at least the threshold parameter
    return  (not is_soft_clipped(read)) and \
            (abs(read['ins_len']) < max_ins and abs(read['ins_len']) > min_ins) and \
            (read['ref_start'] < (pos - sc_len) and read['ref_end'] > (pos + sc_len))

def get_normal_overlap_bases(read,pos):
    return min( [abs(read['ref_start']-pos), abs(read['ref_end']-pos)] )

def is_normal_spanning(read,mate,pos,min_ins,max_ins,sc_len):
    if not is_soft_clipped(read) and not is_soft_clipped(mate):
        if (not read['is_reverse'] and mate['is_reverse']) or (read['is_reverse'] and not mate['is_reverse']):
            return (abs(read['ins_len']) < max_ins and abs(read['ins_len']) > min_ins) and \
                   (read['ref_start'] < (pos + sc_len) and mate['ref_end'] > (pos - sc_len))
    return False

def is_supporting_split_read(read,pos,max_ins,sc_len):
    '''
    Return whether read is a supporting split read.
    Doesn't yet check whether the soft-clip aligns
    to the other side.
    '''
    if read['align_start'] < (params.tr): #a "soft" threshold if it is soft-clipped at the other end        
        return read['ref_end'] > (pos - params.tr) and read['ref_end'] < (pos + params.tr) and \
            (read['len'] - read['align_end'] >= sc_len) and abs(read['ins_len']) < max_ins
    else:
        return read['ref_start'] > (pos - params.tr) and read['ref_start'] < (pos + params.tr) and \
            (read['align_start'] >= sc_len) and abs(read['ins_len']) < max_ins

def is_supporting_split_read_wdir(bp_dir,read,pos,max_ins,sc_len):
    if bp_dir=='+':
        return read['ref_end'] > (pos - params.tr) and read['ref_end'] < (pos + params.tr) and \
            (read['len'] - read['align_end'] >= sc_len) and abs(read['ins_len']) < max_ins
    elif bp_dir=='-':
        return read['ref_start'] > (pos - params.tr) and read['ref_start'] < (pos + params.tr) and \
            (read['align_start'] >= sc_len) and abs(read['ins_len']) < max_ins
    else:
        return False

def is_supporting_split_read_lenient(read,pos,threshold=params.tr):
    '''
    Same as is_supporting_split_read without insert and soft-clip threshold checks
    '''
    if read['align_start'] < 5: #a "soft" threshold if it is soft-clipped at the other end        
        return (read['len'] - read['align_end'] >= threshold) and read['ref_end'] > (pos - threshold) and \
                read['ref_end'] < (pos + threshold)
    else:
        return (read['align_start'] >= threshold) and read['ref_start'] > (pos - threshold) and \
                read['ref_start'] < (pos + threshold)

def get_sc_bases(read,pos):
    '''
    Return the number of soft-clipped bases
    '''
    if read['align_start'] < (params.tr):
        return read['len'] - read['align_end']
    else:
        return read['align_start']

def get_bp_dist(read,bp_pos):
    if read['is_reverse']: 
        return (read['ref_end'] - bp_pos)
    else: 
        return (bp_pos - read['ref_start'])

def points_towards_break(read,pos):
    if read['is_reverse']:
        if read['ref_start'] + params.tr < pos: return False
    else: 
        if read['ref_end'] - params.tr > pos: return False
    return True

def is_supporting_spanning_pair(read,mate,bp1,bp2,inserts,max_ins):
    pos1 = (bp1['start'] + bp1['end']) / 2
    pos2 = (bp2['start'] + bp2['end']) / 2
   
    #ensure this isn't just a regular old spanning pair    
    if read['chrom']==mate['chrom']:
        if read['ref_start']<mate['ref_start']:
            if mate['ref_start']-read['ref_end'] < max_ins: return False
        else:
            if read['ref_start']-mate['ref_end'] < max_ins: return False
    
    #check read orientation
    #spanning reads should always point towards the break
    if not points_towards_break(read,pos1) or not points_towards_break(mate,pos2):
        return False

    ins_dist1 = get_bp_dist(read,pos1)
    ins_dist2 = get_bp_dist(mate,pos2)

    if is_supporting_split_read_lenient(read,pos1):
        if not is_below_sc_threshold(mate): 
            #only allow one soft-clip
            return False
        if abs(ins_dist1)+abs(ins_dist2) < max_ins: 
            return True
    elif is_supporting_split_read_lenient(mate,pos2):
        if not is_below_sc_threshold(read):
            return False
        if abs(ins_dist1)+abs(ins_dist2) < max_ins:
            return True
    else:
        if ins_dist1>=-params.tr and ins_dist2>=-params.tr and abs(ins_dist1)+abs(ins_dist2) < max_ins:
            return True    

    return False

def get_loc_reads(bp,bamf,max_dp):
    loc = '%s:%d:%d' % (bp['chrom'], max(0,bp['start']), bp['end'])
    loc_reads = np.empty([0,len(params.read_dtype)],dtype=params.read_dtype)
    err_code = 0
    try:
        iter_loc = bamf.fetch(region=loc,until_eof=True)
        for x in iter_loc:
            read = read_to_array(x,bamf) 
            if len(np.atleast_1d(read))>0:
                loc_reads = np.append(loc_reads,read)
            if len(loc_reads) > max_dp:
                print('Read depth too high at %s' % loc)
                err_code = 1
                return np.empty(0), err_code
        loc_reads = np.sort(loc_reads,axis=0,order=['query_name','ref_start'])
        loc_reads = np.unique(loc_reads) #remove duplicates
        return loc_reads, err_code
    except ValueError:
        print('Fetching reads failed for loc: %s' % loc)
        err_code = 2
        return np.empty(0), err_code

def reads_to_sam(reads,bam,bp1,bp2,dirout,name):
    '''
    For testing read assignemnts.
    Takes reads from array, matches them to bam 
    file reads by query name and outputs them to Sam
    '''
    bamf = pysam.AlignmentFile(bam, "rb")
    loc1 = '%s:%d:%d' % (bp1['chrom'], bp1['start'], bp1['end'])
    loc2 = '%s:%d:%d' % (bp2['chrom'], bp2['start'], bp2['end'])
    iter_loc1 = bamf.fetch(region=loc1,until_eof=True)
    iter_loc2 = bamf.fetch(region=loc2,until_eof=True)
    
    loc1 = '%s-%d' % (bp1['chrom'], (bp1['start']+bp1['end'])/2)
    loc2 = '%s-%d' % (bp2['chrom'], (bp1['start']+bp1['end'])/2)
    sam_name = '%s_%s-%s' % (name,loc1,loc2)
    if not os.path.exists(dirout):
        os.makedirouts(dirout)
    bam_out = pysam.AlignmentFile('%s/%s.sam'%(dirout,sam_name), "w", header=bamf.header)
    
    for x in iter_loc1:
        if len(reads)==0:
            break
        if x.query_name in reads:
            bam_out.write(x)
            bam_out.write(bamf.mate(x))
            idx = int(np.where(reads==x.query_name)[0])
            reads = np.delete(reads,idx)

    for x in iter_loc2:
        if len(reads)==0:
            break
        if x.query_name in reads:
            bam_out.write(x)
            bam_out.write(bamf.mate(x))
            idx = int(np.where(reads==x.query_name)[0])
            reads = np.delete(reads,idx)

    bamf.close()
    bam_out.close()

def windowed_norm_read_count(loc_reads,inserts,min_ins,max_ins):
    '''
    Counts normal non-soft-clipped reads within window range
    '''
    cnorm = 0
    for idx,r in enumerate(loc_reads):
        if idx+1 >= len(loc_reads):
            break    
        r1 = np.array(loc_reads[idx],copy=True)
        r2 = np.array(loc_reads[idx+1],copy=True)
        if r1['query_name']!=r2['query_name'] or r1['chrom']!=r2['chrom']:
            continue
        ins_dist = r2['ref_end']-r1['ref_start']
        facing = not r1['is_reverse'] and r2['is_reverse']
        if not is_soft_clipped(r1) and not is_soft_clipped(r2) and facing and ins_dist > min_ins and ins_dist < max_ins:
            cnorm = cnorm + 2
    return cnorm

def get_loc_counts(bp,loc_reads,pos,rc,reproc,split,norm,min_ins,max_ins,sc_len,bp_num=1):
    for idx,x in enumerate(loc_reads):
        if idx+1 >= len(loc_reads):            
            break        
        r1 = loc_reads[idx]
        r2 = loc_reads[idx+1] if (idx+2)<=len(loc_reads) else None
        if is_normal_non_overlap(r1,r2,pos,min_ins,max_ins):
            continue
        elif is_normal_across_break(x,pos,min_ins,max_ins,sc_len):        
            norm = np.append(norm,r1)            
            split_norm = 'bp%d_split_norm'%bp_num
            norm_olap = 'bp%d_norm_olap_bp'%bp_num
            rc[split_norm] = rc[split_norm]+1 
            rc[norm_olap] = rc[norm_olap]+get_normal_overlap_bases(x,pos)
        elif is_supporting_split_read(x,pos,max_ins,sc_len):
            split_supp = 'bp%d_split'%bp_num
            split_cnt = 'bp%d_sc_bases'%bp_num
            if bp['dir'] in ['+','-']:
                if is_supporting_split_read_wdir(bp['dir'],x,pos,max_ins,sc_len):
                    split = np.append(split,x)            
                    rc[split_supp] = rc[split_supp]+1 
                    rc[split_cnt]  = rc[split_cnt]+get_sc_bases(x,pos)                    
                else:
                    reproc = np.append(reproc,x) #may be spanning support or anomalous
        elif r2!=None and r1['query_name']==r2['query_name'] and is_normal_spanning(r1,r2,pos,min_ins,max_ins,sc_len):
            norm = np.append(norm,r1)            
            norm = np.append(norm,r2)            
            span_norm = 'bp%d_span_norm'%bp_num
            rc[span_norm] = rc[span_norm]+1 
        else:
            reproc = np.append(reproc,x) #may be spanning support or anomalous
    return rc, reproc, split, norm

def bp_dir_matches_read_orientation(bp,pos,read):
    if bp['dir']=='+':
        return read['ref_start'] < pos and not read['is_reverse']
    elif bp['dir']=='-':
        return read['ref_end'] > pos and read['is_reverse']

def validate_spanning_orientation(bp1,bp2,r1,r2):
    pos1 = (bp1['start'] + bp1['end']) / 2
    pos2 = (bp2['start'] + bp2['end']) / 2
    
    r1_correct = bp_dir_matches_read_orientation(bp1,pos1,r1)
    r2_correct = bp_dir_matches_read_orientation(bp2,pos2,r2)
    
    return r1_correct and r2_correct

def get_spanning_counts(reproc,rc,bp1,bp2,inserts,min_ins,max_ins):
    pos1 = (bp1['start'] + bp1['end']) / 2
    pos2 = (bp2['start'] + bp2['end']) / 2
       
    reproc = np.sort(reproc,axis=0,order=['query_name','ref_start'])
    reproc = np.unique(reproc) #remove dups
    anomalous = np.empty([0,len(params.read_dtype)],dtype=params.read_dtype)    
    span_bp1 = np.empty([0,len(params.read_dtype)],dtype=params.read_dtype)
    span_bp2 = np.empty([0,len(params.read_dtype)],dtype=params.read_dtype)
    
    for idx,x in enumerate(reproc):
        if idx+1 >= len(reproc):
            break        
        if reproc[idx+1]['query_name'] != reproc[idx]['query_name']:
            #not paired
            continue
        mate = np.array(reproc[idx+1],copy=True)
        r1 = np.array(x,copy=True)
        r2 = np.array(mate,copy=True)
        #if read corresponds to bp2 and mate to bp1, switch their order
        if (bp1['chrom']!=bp2['chrom'] and r1['chrom']==bp2['chrom']) or \
            (pos1 > pos2 and bp1['chrom']==bp2['chrom']):
            r1 = mate
            r2 = np.array(x,copy=True)
        if is_supporting_spanning_pair(r1,r2,bp1,bp2,inserts,max_ins):        
            if bp1['dir'] in ['+','-'] and bp2['dir'] in ['-','+']:
                if validate_spanning_orientation(bp1,bp2,r1,r2):
                    span_bp1 = np.append(span_bp1,r1)
                    span_bp2 = np.append(span_bp2,r2)
                    rc['spanning'] = rc['spanning']+1
                else:
                    anomalous = np.append(anomalous,r1)
                    anomalous = np.append(anomalous,r2)
        else:
            anomalous = np.append(anomalous,r1)
            anomalous = np.append(anomalous,r2)
    return rc,span_bp1,span_bp2,anomalous

#def recount_split_reads(split_reads,pos,bp_dir,max_ins,sc_len):
#    split_count = 0
#    split_bases = 0
#    for idx,x in enumerate(split_reads):
#        if is_supporting_split_read_wdir(bp_dir,x,pos,max_ins,sc_len):
#            split_count += 1
#            split_bases += get_sc_bases(x,pos)
#    return split_count,split_bases        

def get_sv_read_counts(row,bam,inserts,max_dp,min_ins,max_ins,sc_len,out,split_reads,span_reads,anom_reads):
    
    sv_id, bp1_chr, bp1_pos, bp1_dir, bp2_chr, bp2_pos, bp2_dir, sv_class = [h[0] for h in params.sv_dtype]    
    bp1 = np.array((row[bp1_chr],row[bp1_pos]-params.window,
                    row[bp1_pos]+params.window,row[bp1_dir]),dtype=params.bp_dtype)
    bp2 = np.array((row[bp2_chr],row[bp2_pos]-params.window,
                    row[bp2_pos]+params.window,row[bp2_dir]),dtype=params.bp_dtype)
    pos1, pos2 = row[bp1_pos], row[bp2_pos]

    rc = np.zeros(1,dtype=params.sv_out_dtype)[0]
    rc['bp1_chr'],rc['bp1_pos'],rc['bp1_dir']=row[bp1_chr],row[bp1_pos],row[bp1_dir]
    rc['bp2_chr'],rc['bp2_pos'],rc['bp2_dir']=row[bp2_chr],row[bp2_pos],row[bp2_dir]
    rc['ID'],rc['classification']=row[sv_id],row[sv_class]
   
    if row[bp1_dir] not in ['+','-'] or row[bp2_dir] not in ['+','-']:
        #one or both breaks don't have a valid direction
        return rc, split_reads, span_reads, anom_reads

    bamf = pysam.AlignmentFile(bam, "rb")
    loc1_reads, err_code1 = get_loc_reads(bp1,bamf,max_dp)    
    loc2_reads, err_code2 = get_loc_reads(bp2,bamf,max_dp)    
    bamf.close()
   
    if not (err_code1==0 and err_code2==0) or (len(loc1_reads)==0 or len(loc2_reads)==0):
        sv_class = str(row['classification'])
        if err_code1 == 1 or err_code2 == 1:
            rc['classification'] = 'HIDEP' if sv_class=='' else sv_class+';HIDEP' 
            return rc, split_reads, span_reads, anom_reads
        elif err_code1 == 2 or err_code2 == 2:
            rc['classification'] = 'READ_FETCH_FAILED' if sv_class=='' else sv_class+';READ_FETCH_FAILED'
            return rc, split_reads, span_reads, anom_reads
        else:
            rc['classification'] = 'NO_READS' if sv_class=='' else sv_class+';NO_READS'
            return rc, split_reads, span_reads, anom_reads
    
    reproc = np.empty([0,len(params.read_dtype)],dtype=params.read_dtype)    
    rc['bp1_total_reads'] = len(loc1_reads)
    rc['bp2_total_reads'] = len(loc2_reads)

    split_bp1 = np.empty([0,len(params.read_dtype)],dtype=params.read_dtype)
    split_bp2 = np.empty([0,len(params.read_dtype)],dtype=params.read_dtype)
    norm = np.empty([0,len(params.read_dtype)],dtype=params.read_dtype)
    
    rc, reproc, split_bp1, norm = get_loc_counts(bp1,loc1_reads,pos1,rc,reproc,
                                                 split_bp1,norm,min_ins,max_ins,sc_len)
    rc, reproc, split_bp2, norm = get_loc_counts(bp2,loc2_reads,pos2,rc,reproc,
                                                 split_bp2,norm,min_ins,max_ins,sc_len,2)
    rc['bp1_win_norm'] = windowed_norm_read_count(loc1_reads,inserts,min_ins,max_ins)
    rc['bp2_win_norm'] = windowed_norm_read_count(loc2_reads,inserts,min_ins,max_ins)    
 
    rc, span_bp1, span_bp2, anomalous = get_spanning_counts(reproc,rc,bp1,bp2,inserts,min_ins,max_ins)
    spanning = span_bp1
    spanning = np.concatenate([span_bp1,span_bp2]) if (len(span_bp1)>0 and len(span_bp2)>0) else spanning
    spanning = span_bp2 if len(span_bp1)==0 else spanning
    span_reads = np.append(span_reads,spanning)

    split = split_bp1
    split = np.concatenate([split_bp1,split_bp2]) if (len(split_bp1)>0 and len(split_bp2)>0) else split
    split = split_bp2 if len(split_bp1)==0 else split
    split_reads = np.append(split_reads,split)

    rc['anomalous'] = len(anomalous)
    anom_reads = np.append(anom_reads,anomalous)

    print('processed %d reads at loc1; %d reads at loc2' % (len(loc1_reads),len(loc2_reads)))
    return rc, split_reads, span_reads, anom_reads

def get_params(bam,mean_dp,max_cn,rlen,insert_mean,insert_std,out):
    inserts = [insert_mean,insert_std]
    if rlen<0:
        rlen = bamtools.estimateTagSize(bam)
    if inserts[0]<0 or inserts[1]<0:
        inserts = bamtools.estimateInsertSizeDistribution(bam)
        inserts = (max(rlen*2,inserts[0]),inserts[1])
    else:
        inserts[0] = inserts[0]
    
    max_dp = ((mean_dp*(params.window*2))/rlen)*max_cn
    max_ins = inserts[0]+(3*inserts[1]) #max fragment size = mean fragment len + (fragment std * 3)
    min_ins = rlen*2
    
    with open('%s_params.txt'%out,'w') as outp:
        outp.write('read_len\tinsert_mean\tinsert_std\tinsert_min\tinsert_max\tmax_dep\n')
        outp.write('%d\t%f\t%f\t%f\t%f\t%d'%(rlen,inserts[0],inserts[1],min_ins,max_ins,max_dp))

    return rlen, inserts, max_dp, max_ins, min_ins

def write_anomalous_read_to_bam(bam,split_reads,span_reads,anom_reads,out):
    print('Writing anom reads to file')
    split_reads = np.unique(split_reads['query_name'])
    span_reads = np.unique(span_reads['query_name'])
    anom_reads = np.unique(anom_reads['query_name'])

    # need to filter out any reads that were at any point marked as valid spanning
    anom_reads = np.array([x for x in anom_reads if x not in split_reads])
    anom_reads = np.array([x for x in anom_reads if x not in span_reads])

    bamf = pysam.AlignmentFile(bam, "rb")
    index = pysam.IndexedReads(bamf)
    index.build()
    anom_bam = pysam.AlignmentFile("%s_anom_reads.bam" % out, "wb", template=bamf)
    for read_name in anom_reads:
        for read in index.find(read_name):
            anom_bam.write(read)
    anom_bam.close()

def recount_anomalous_reads(bam,outname,anom_reads,max_dp):
    print('Recounting anomalous reads')   
    anom_reads = np.unique(anom_reads['query_name'])
    sv_proc = np.genfromtxt(outname,delimiter='\t',names=True,dtype=params.sv_out_dtype,invalid_raise=False)
    for idx,row in enumerate(sv_proc):
        sv_id, bp1_chr, bp1_pos, bp1_dir, bp2_chr, bp2_pos, bp2_dir, sv_class = [h[0] for h in params.sv_dtype]    
        bp1 = np.array((row[bp1_chr],row[bp1_pos]-params.window,
                        row[bp1_pos]+params.window,row[bp1_dir]),dtype=params.bp_dtype)
        bp2 = np.array((row[bp2_chr],row[bp2_pos]-params.window,
                        row[bp2_pos]+params.window,row[bp2_dir]),dtype=params.bp_dtype)

        bamf = pysam.AlignmentFile(bam, "rb")
        loc1_reads, err_code1 = get_loc_reads(bp1,bamf,max_dp)
        loc2_reads, err_code2 = get_loc_reads(bp2,bamf,max_dp)
        bamf.close()
        
        if err_code1==0 and err_code2==0:
            anom1 = [ x['query_name'] for x in loc1_reads if x['query_name'] in anom_reads]
            anom2 = [ x['query_name'] for x in loc2_reads if x['query_name'] in anom_reads]
            anom = np.concatenate([anom1,anom2])
            anom_count = len(np.unique(anom))
            sv_proc[idx]['anomalous'] = anom_count
            print('found %d anomalous reads at %s:%d|%s:%d' % (anom_count,row[bp1_chr],row[bp1_pos],row[bp2_chr],row[bp2_pos]))
    
    with open(outname,'w') as outf:
        header_out = [h[0] for idx,h in enumerate(params.sv_out_dtype)]
        writer = csv.writer(outf,delimiter='\t',quoting=csv.QUOTE_NONE)
        writer.writerow(header_out)
        for row in sv_proc:   
            writer = csv.writer(outf,delimiter='\t',quoting=csv.QUOTE_NONE)
            writer.writerow(row)            

def proc_svs(args):
    
    svin         = args.svin
    bam          = args.bam
    out          = args.out
    mean_dp      = float(args.mean_dp)
    sc_len       = int(args.sc_len)
    max_cn       = int(args.max_cn)
    rlen         = int(args.rlen)
    insert_mean  = float(args.insert_mean)
    insert_std   = float(args.insert_std)
    write_anom   = args.write_anom

    outname = '%s_svinfo.txt'%out
    dirname = os.path.dirname(out)
    if dirname!='' and not os.path.exists(dirname):
        os.makedirs(dirname)

    rlen, inserts, max_dp, max_ins, min_ins = get_params(bam, mean_dp, max_cn, rlen, insert_mean, insert_std, out)

    # write header output
    header_out = [h[0] for idx,h in enumerate(params.sv_out_dtype)]
    
    with open(outname,'w') as outf:        
        writer = csv.writer(outf,delimiter='\t',quoting=csv.QUOTE_NONE)
        writer.writerow(header_out)

    split_reads = np.empty([0,len(params.read_dtype)],dtype=params.read_dtype)
    span_reads = np.empty([0,len(params.read_dtype)],dtype=params.read_dtype)
    anom_reads = np.empty([0,len(params.read_dtype)],dtype=params.read_dtype)
    
    sv_id, bp1_chr, bp1_pos, bp1_dir, bp2_chr, bp2_pos, bp2_dir, sv_class = [h[0] for h in params.sv_dtype]    
    svs = np.genfromtxt(svin,delimiter='\t',names=True,dtype=None,invalid_raise=False)
    print("Extracting data from %d SVs"%len(svs))
    for row in svs:        
        sv_prop = row[bp1_chr],row[bp1_pos],row[bp2_chr],row[bp2_pos]
        sv_str = '%s:%d|%s:%d'%sv_prop
        print('processing %s'%sv_str)

        sv_rc, split_reads, span_reads, anom_reads = get_sv_read_counts(row,bam,inserts,max_dp,min_ins,max_ins,
                                                           sc_len,out,split_reads,span_reads,anom_reads)

        norm1 = int(sv_rc['bp1_split_norm'] + sv_rc['bp1_span_norm'])
        norm2 = int(sv_rc['bp2_split_norm'] + sv_rc['bp2_span_norm'])
        support = float(sv_rc['bp1_split'] + sv_rc['bp2_split'] + sv_rc['spanning'])
    
        sv_rc['norm1'] = norm1 
        sv_rc['norm2'] = norm2
        sv_rc['support'] = support
        sv_rc['vaf1'] = support / (support + norm1) if support!=0 else 0
        sv_rc['vaf2'] = support / (support + norm2) if support!=0 else 0            

        with open(outname,'a') as outf:
            writer = csv.writer(outf,delimiter='\t',quoting=csv.QUOTE_NONE)
            writer.writerow(sv_rc)
   
    if write_anom:
        write_anomalous_read_to_bam(bam,split_reads,span_reads,anom_reads,out)
        sv_proc = recount_anomalous_reads(bam,outname,anom_reads,max_dp)
