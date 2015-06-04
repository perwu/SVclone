import numpy as np

# PREPROCESSING PARAMETERS
tr      = 5    # "wobble length" tolerance threshold which we allow breaks to be inexact
window  = 500  # base-pairs considered to the left and right of the break

# parameters extracted for each read from BAMs
read_dtype = [('query_name', 'S150'), ('chrom', 'S50'), ('ref_start', int), ('ref_end', int), \
              ('align_start', int), ('align_end', int), ('len', int), ('ins_len', int), ('is_reverse', np.bool)]

# PHYL constant parameters
subclone_threshold      = 0.05 # throw out any subclones with frequency lower than this value
subclone_sv_prop        = 0.08 # remove any cluster groups with fewer than this proportion of SVs clustering together
subclone_diff           = 0.10 # merge any clusters within this range
tolerate_svloss         = 0.30 # recluster if we lose more than x% of SVs from clustering/filtering
clus_limit              = 20 # maximum number of clusters generated by dirichlet process
#beta_shape              = 0.8
#beta_rate               = 1/0.8
