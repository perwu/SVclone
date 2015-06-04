#!/usr/bin/env python

'''
Commandline input for running SV 
'''

import argparse
import sys
from . import run

parser = argparse.ArgumentParser(prefix_chars='--')
parser.add_argument("-s","--samples",dest="samples",
    help="Required: Sample names (comma separated if multiple), not including germline.")
parser.add_argument("-i","--input",dest="procd_svs",
    help="Required: Processed structural variation input (comma separated if multiple).")
parser.add_argument("-g","--germline",dest="germline",default="",
    help="Germline SVs. If not provided, will assume all SVs are somatic.")
parser.add_argument("-c","--cnvs",dest="cnvs",default="",
    help="Phased copy-number states from Battenberg (comma separated if multiple). " + \
            "If not provided, all SVs assumed copy-neutral.")
parser.add_argument("-r","--readlen",dest="rlen",default="100",
    help="Read length of samples. May be comma separated per sample. "+ \
            "If one value is given, read len is the same for all samples")
parser.add_argument("-v","--insert",dest="insert",default="",
    help="Mean insert size, where value = fragment size - (2 * read len). " +
            "Comma separated if multuple samples.")
parser.add_argument("-p","--purity",dest="pi",default="1.",
    help="Tumour purities for all samples given. A single parameter assumes " +
            "uniform purity for all samples. No parameter assumes 100% purity.")
parser.add_argument("-y","--ploidy",dest="ploidy",default="1.0",
    help="Tumour ploidy. The defailt (1.0) ignores ploidy.")
parser.add_argument("-o","--outdir",dest="outdir",default=".",
        help="Output directory. Default: current directory")
parser.add_argument("-n","--n_runs",dest="n_runs",default=10,type=int,
        help="Number of times to run whole rounds of sampling.")
parser.add_argument("-t","--n_iter",dest="n_iter",default=10000,type=int,
        help="Number of MCMC iterations.")
parser.add_argument("--burn",dest="burn",default=0,type=int,
        help="Burn-in for MCMC (default 0.)")
parser.add_argument("--thin",dest="thin",default=1,type=int,
        help="Thinning parameter for MCMC (default 1.)")
parser.add_argument("--beta",dest="beta",default="8,1/0.05,0.1",type=str,
        help="Comma separated; first two values etermine the parameters used for " + 
             "Dirichlet Processes' gamma function. Third value determines the starting value.")
args = parser.parse_args()

samples = args.samples
svs     = args.procd_svs
gml     = args.germline
cnvs    = args.cnvs
out     = args.outdir
rlen    = args.rlen
insert  = args.insert
pi      = args.pi
ploidy  = args.ploidy
n_runs  = args.n_runs
n_iter  = args.n_iter
burn    = args.burn
thin    = args.thin
beta    = args.beta

def proc_arg(arg,n_args=1,of_type=str):
    arg = str.split(arg,',')
    arg = arg * n_args if len(arg)==1 else arg
    if of_type==int or of_type==float:
        return map(eval,arg)
    else:
        return map(of_type,arg)

if __name__ == '__main__':
    import ipdb
    if insert=="":
        print("Inserts not provided, assuming insert length equals read length")
        insert=rlen
    samples = proc_arg(samples)
    n = len(samples)
    svs = proc_arg(svs)
    cnvs = proc_arg(cnvs)
    if len(svs)!=n or len(cnvs)!=n:
        raise ValueError("Number of samples does not match number of input files")
    rlen = proc_arg(rlen,n,int)
    insert = proc_arg(insert,n,float)
    pi = proc_arg(pi,n,float)
    beta = proc_arg(beta,3,float)
    for p in pi: 
        if p<0 or p>1:
            raise ValueError("Tumour purity value not between 0 and 1!")
    ploidy = proc_arg(ploidy,n,float)
    run.run(samples,svs,gml,cnvs,rlen,insert,pi,ploidy,out,n_runs,n_iter,burn,thin,beta)
