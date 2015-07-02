'''
Takes an SV matrix and attempts to build a phylogeny
'''
import subprocess
import ipdb
import sys
import os
import pandas as pd
import scipy as sp
import scipy.stats

from . import cluster
from . import parameters as param

import numpy as np
from numpy import loadtxt
from numpy import zeros

def index_max(values):
    return max(xrange(len(values)),key=values.__getitem__)

def mean_confidence_interval(phi_trace, alpha=0.1):
    n = len(phi_trace)
    m, se = np.mean(phi_trace), np.std(phi_trace,ddof=1)/(np.sqrt(n))
    h = se * sp.stats.t._ppf((1+(1-alpha))/2., n-1)
    return round(m,4), round(m-h,4), round(m+h,4)

def merge_clusters(df,clus_info,clus_merged,clus_members,cparams,out,num_iters,burn,thin):
    if len(clus_info)==1:
        return (clus_info,clus_members)

    to_del = []
    for idx,ci in clus_info.iterrows():
        if idx+1 >= len(clus_info):
            clus_merged.loc[idx] = clus_info.loc[idx]
            break
        cn = clus_info.loc[idx+1]
        if abs(ci.phi - float(cn.phi)) < param.subclone_diff:
            print("\nReclustering similar clusters...")
            new_size = ci['size'] + cn['size']
            new_members = np.concatenate([clus_members[idx],clus_members[idx+1]])
            trace = cluster.recluster(df.loc[new_members], cparams[0], cparams[1], \
                    cparams[2], cparams[3], num_iters, burn, thin)
            phis = mean_confidence_interval(trace)
            clus_members[idx] = new_members
            to_del.append(idx+1)
            df_trace = pd.DataFrame(np.transpose(trace[:]))
            df_trace.to_csv('%s/reclus%d_phi_trace.txt'%(out,int(ci.clus_id)),sep='\t',index=False)
            clus_merged.loc[idx] = np.array([ci.clus_id,new_size,phis[0],phis[1],phis[2]])
            print('\n')
            if idx+2 < len(clus_info):
                clus_merged.loc[idx+2:] = clus_info.loc[idx+2:]
            break
        else:
            clus_merged.loc[idx] = clus_info.loc[idx]
    
    if len(to_del)>0:
        clus_members = np.delete(clus_members,to_del)
        # clean and reorder merged dataframe
        clus_merged = clus_merged[pd.notnull(clus_merged['clus_id'])]
        clus_merged = clus_merged.sort('phi',ascending=False)
        clus_merged.index = range(len(clus_merged))
        print("\nMerged clusters")
        print('Input')
        print(clus_info)
        print('Merged')
        print(clus_merged)

    if len(clus_info) == len(clus_merged):
        return (clus_merged,clus_members)
    else: 
        new_df = pd.DataFrame(columns=clus_merged.columns,index=clus_merged.index)
        return merge_clusters(df,clus_merged,new_df,clus_members,cparams,out,num_iters,burn,thin)

def run_clust(df,pi,rlen,insert,ploidy,num_iters,burn,thin,beta):
    mcmc = cluster.cluster(df,pi,rlen,insert,ploidy,num_iters,burn,thin,beta)
    npoints = len(df.spanning.values)

    # assign points to highest probabiliy cluster
    z_trace = mcmc.trace('z')[:]
    clus_counts = [np.bincount(z_trace[:,i]) for i in range(npoints)]
    clus_max_prob = [index_max(c) for c in clus_counts]
    clus_mp_counts = np.bincount(clus_max_prob)
    clus_idx = np.nonzero(clus_mp_counts)[0]
    clus_mp_counts = clus_mp_counts[clus_idx]
    
    # cluster distribution
    clus_info = pd.DataFrame(clus_idx,columns=['clus_id'])
    clus_info['size'] = clus_mp_counts

    # get cluster means
    center_trace = mcmc.trace("phi_k")[:]
    
    # center_trace = center_trace[len(center_trace)/4:]
    # param.plot_clusters(center_trace,npoints,clus_idx)
    
    #phis = np.array([np.mean(center_trace[:,cid]) for cid in clus_info.clus_id.values])
    phis = np.array([mean_confidence_interval(center_trace[:,cid]) for cid in clus_info.clus_id.values])
    clus_info['phi'] = phis[:,0]
    clus_info['phi_low_conf'] = phis[:,1]
    clus_info['phi_hi_conf'] = phis[:,2]
    clus_info = clus_info.sort('phi',ascending=False)

    # cluster filtering
    #clus_info = clus_info[clus_info['phi'].values>(param.subclone_threshold/2)]
    #clus_info = clus_info[sum(clus_info['size'].values)*param.subclone_sv_prop<clus_info['size'].values]
    #clus_counts = [np.array(c)[clus_ids] for c in clus_counts]
    #clus_max_prob = np.array(clus_max_prob)[clus_ids]
    
    clus_ids = clus_info.clus_id.values
    clus_members = np.array([np.where(np.array(clus_max_prob)==i)[0] for i in clus_ids])

    # probability of being in each cluster
    #probs = [np.array(x)[clus_info.clus_id.values] for x in probs]
    #clus_counts = [np.array(c)[clus_info.clus_id.values] for c in clus_counts]
    #probs = [map(lambda x: round(x[0]/x[1],4),zip(x,[float(sum(x))]*len(x))) for x in clus_counts]
    #df_probs = pd.DataFrame(probs).fillna(0)
    
    col_names = map(lambda x: 'cluster'+str(x),clus_ids)
    df_probs = pd.DataFrame(clus_counts,dtype=float)[clus_ids].fillna(0)
    df_probs = df_probs.apply(lambda x: x/sum(x),axis=1)
    df_probs.columns = col_names
    df_pos = ['bp1_chr','bp1_pos','bp2_chr','bp2_pos']
    df_probs = df[df_pos].join(df_probs)

    # cluster certainty
    clus_max_df = pd.DataFrame(clus_max_prob,columns=['most_likely_assignment'])
    phi_cols = ["average_ccf","90_perc_CI_lo","90_perc_CI_hi"]
    phi_matrix = pd.DataFrame(phis[:],index=clus_ids,columns=phi_cols).loc[clus_max_prob]
    phi_matrix.index = range(len(phi_matrix))
    ccert = df[df_pos].join(clus_max_df).join(phi_matrix)

    clus_info.index = range(len(clus_info))
    print(clus_info)
    return clus_info,center_trace,z_trace,clus_members,df_probs,ccert

def dump_trace(clus_info,center_trace,outf):
    traces = np.array([center_trace[:,cid] for cid in clus_info.clus_id.values])
    df_traces = pd.DataFrame(np.transpose(traces),columns=clus_info.clus_id)
    df_traces.to_csv(outf,sep='\t',index=False)

def write_out_files(df,clus_info,clus_members,df_probs,clus_cert,clus_out_dir,sample):
    
    clus_info.to_csv('%s/clusters.txt'%(clus_out_dir),sep='\t',index=False)
    with open('%s/number_of_clusters.txt'%clus_out_dir,'w') as outf:
        outf.write("sample\tclusters\n")
        outf.write('%s\t%d\n'%(sample,len(clus_info)))
    
    cn_dtype =  [('chr1','S50'),
                 ('pos1',int),
                 ('total_copynumber1',int),
                 ('no_chrs_bearing_mutation1',int),
                 ('chr2','S50'),
                 ('pos2',int),
                 ('total_copynumber2',int),
                 ('no_chrs_bearing_mutation2',int)]

    cmem = np.hstack(clus_members)
    cn_vect = np.empty((0,len(cmem)),dtype=cn_dtype)
    clus_svs = df.loc[cmem].copy()
    
    for idx,sv in clus_svs.iterrows():

        maj_cn1,min_cn1 = sv['bp1_maj_cnv'],sv['bp1_min_cnv']
        maj_cn2,min_cn2 = sv['bp2_maj_cnv'],sv['bp2_min_cnv']

        maj_cn1 = int(maj_cn1) if maj_cn1!="" else np.nan
        min_cn1 = int(min_cn1) if min_cn1!="" else np.nan
        maj_cn2 = int(maj_cn2) if maj_cn2!="" else np.nan
        min_cn2 = int(min_cn2) if min_cn2!="" else np.nan
        tot_cn1 = maj_cn1+min_cn1 if not np.isnan(maj_cn1) and not np.isnan(min_cn1) else np.nan
        tot_cn2 = maj_cn1+min_cn2 if not np.isnan(maj_cn2) and not np.isnan(min_cn2) else np.nan

        bp1_chr = str(sv['bp1_chr'])
        bp1_pos = int(sv['bp1_pos'])
        bp2_chr = str(sv['bp2_chr'])
        bp2_pos = int(sv['bp2_pos'])

        cn_new_row = np.array([(bp1_chr,bp1_pos,tot_cn1,min_cn1,bp2_chr,bp2_pos,tot_cn2,min_cn2)],dtype=cn_dtype)
        cn_vect = np.append(cn_vect,cn_new_row)

    pd.DataFrame(cn_vect).to_csv('%s/%s_copynumber.txt'%(clus_out_dir,sample),sep='\t',index=False)
    df_probs.to_csv('%s/%s_assignment_probability_table.txt'%(clus_out_dir,sample),sep='\t',index=False)
    clus_cert.to_csv('%s/%s_cluster_certainty.txt'%(clus_out_dir,sample),sep='\t',index=False)

def infer_subclones(sample,df,pi,rlen,insert,ploidy,out,n_runs,num_iters,burn,thin,beta):

    clus_info,center_trace,ztrace,clus_members = None,None,None,None

    for i in range(n_runs):
        print("Cluster run: %d"%i)
        clus_info,center_trace,z_trace,clus_members,df_probs,clus_cert = run_clust(df,pi,rlen,insert,ploidy,num_iters,burn,thin,beta)
        sv_loss = 1-(sum(clus_info['size'])/float(len(df)))

        clus_out_dir = '%s/run%d'%(out,i)
        if not os.path.exists(clus_out_dir):
            os.makedirs(clus_out_dir)

        with open("%s/warnings.txt"%clus_out_dir,'w') as warn:
            warn_str = ""
            if len(clus_info) < 1:
                warn_str = "Warning! Could not converge on any major clusters. Skipping.\n"
                warn.write(warn_str)
                print('\n'+warn_str)
                continue
            if sv_loss > param.tolerate_svloss:
                warn_str = "Warning! Lost %f of SVs.\n" % sv_loss
            #if (clus_info.phi.values[0]*2.)>(1+param.subclone_diff):
            #    warn_str = warn_str + "Warning! Largest VAF cluster exceeds 0.5.\n"
            warn.write(warn_str)
            print('\n'+warn_str)
        
        dump_trace(clus_info,center_trace,'%s/phi_trace.txt'%clus_out_dir)
        dump_trace(clus_info,z_trace,'%s/z_trace.txt'%clus_out_dir)
#        clus_info.to_csv('%s/clusters_premerged.txt'%(clus_out_dir),sep='\t',index=False)
#        clus_init = pd.DataFrame(columns=clus_info.columns,index=clus_info.index)
#        clus_info,clus_new_members = \
#            merge_clusters(df,clus_info,clus_init,clus_members.copy(),[pi,rlen,insert,ploidy],clus_out_dir,num_iters,burn,thin)
#        
#        print("\nFiltered & merged clusters")
#        clus_info.clus_id = map(int,clus_info.clus_id.values)
#        clus_info['size'] = map(int,clus_info['size'].values)
#        print(clus_info)
        
        write_out_files(df,clus_info,clus_members,df_probs,clus_cert,clus_out_dir,sample)
