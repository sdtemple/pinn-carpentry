#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Oct 31 15:15:34 2021

@author: feng779
"""

import numpy as np
from scipy.interpolate import interp1d
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from mpl_toolkits.axes_grid1 import make_axes_locatable
import h5py
from datetime import datetime
import time
from pyDOE import lhs
import tensorflow as tf
import pickle
import os
import seaborn as sns

import warnings
warnings.filterwarnings("ignore")

from SVE_module_dynamic_h import SVE as SVE_standard
from SVE_module_dynamic_h_mff_ts import SVE as SVE_mff

import pdb


def time_convert(intime):
    """
    function to convert the time from string to datetime
    """
    Nt = intime.shape[0]
    outtime = []
    for t in range(Nt):
        timestr = intime[t].decode('utf-8')
        outtime.append(datetime.strptime(timestr, '%d%b%Y %H:%M:%S'))
    return outtime

def add_noise(insignal):
    """
    https://stackoverflow.com/questions/14058340/adding-noise-to-a-signal-in-python
    """

    target_snr_db = 500
    # Calculate signal power and convert to dB 
    sig_avg = np.mean(insignal)
    sig_avg_db = 10 * np.log10(sig_avg)
    # Calculate noise according to [2] then convert to watts
    noise_avg_db = sig_avg_db - target_snr_db
    noise_avg = 10 ** (noise_avg_db / 10)
    # Generate an sample of white noise
    mean_noise = 0
    noise = np.random.normal(mean_noise, np.sqrt(noise_avg), len(insignal))
    # Noise up the original signal
    sig_noise = insignal + noise
    return sig_noise

if __name__ == "__main__": 
    
    """
    step one: read model output
    """
    hdf_filename = 'HEC-RAS/case4/MixedFlow.p02.hdf'
    hf = h5py.File(hdf_filename,'r') 
    
    attrs = hf['Geometry']['Cross Sections']['Attributes'][:]
    staid = []
    eles = []
    reach_len = []
    for attr in attrs:
        staid.append(attr[2].decode('utf-8'))
        eles.append(attr[14])
        reach_len.append(attr[6])

    coor = np.cumsum(np.array(reach_len[:-1]))
    coor = [0] + coor.tolist()
    coor = coor[::-1]
    eles = np.array(eles)
    slope = np.gradient( eles, coor)
     
    water_surface = hf['Results']['Unsteady']['Output']["/Results/Unsteady/Output"]['Output Blocks']['Base Output']["Unsteady Time Series"]["Cross Sections"]['Water Surface'][:]
    velocity_total = hf['Results']['Unsteady']['Output']["/Results/Unsteady/Output"]['Output Blocks']['Base Output']["Unsteady Time Series"]["Cross Sections"]['Velocity Total'][:]
    Timestamp = hf['Results']['Unsteady']['Output']["/Results/Unsteady/Output"]['Output Blocks']['Base Output']["Unsteady Time Series"]['Time Date Stamp'][:]
    time_model = time_convert(Timestamp)
    
    water_depth = water_surface - eles[None,:]
    b = 10 # channel width
    
    warmup_step = 1
    velocity_total = velocity_total[warmup_step:]
    water_depth = water_depth[warmup_step:]

    ## dnstrm reach
    ind_dnstrm = 102 
    velocity_total = velocity_total[:,ind_dnstrm:]
    water_depth = water_depth[:,ind_dnstrm:]
    slope = slope[ind_dnstrm:]
    eles = eles[ind_dnstrm:]
    eles = eles - eles[-1]

    Nt = water_depth.shape[0]
    Nx = water_depth.shape[1]

    Nt_train = water_depth.shape[0]
    Nf_train = 70000  # This is not used, all collocation points are used
    layers = [2] + 4*[1*64] + [2]
    
    t = np.arange(Nt_train)[:,None]
    x = np.array(coor[::-1])[:,None]
    x = x[ind_dnstrm:] - x[ind_dnstrm] ## dnstrm reach
    u_exact = velocity_total[:Nt_train,:]
    h_exact = water_depth[:Nt_train,:]
    
    X, T = np.meshgrid(x,t)
    
    X_star = np.hstack((X.flatten()[:,None], T.flatten()[:,None]))
    u_star = u_exact.flatten()[:,None]    
    h_star = h_exact.flatten()[:,None]  
    
    # Doman bounds
    lb = X_star.min(0)
    ub = X_star.max(0)
    
    ##
    tsteps = [0] + [Nt-1]
    for i, tstep in enumerate(tsteps):
        xx1_ = np.hstack((X[tstep:tstep+1,:].T, T[tstep:tstep+1,:].T))
        hh1_ = add_noise(h_exact[tstep:tstep+1,:].T)
        if i == 0:
            xx1 = xx1_
            hh1 = hh1_
        else:
            xx1 = np.vstack((xx1, xx1_))
            hh1 = np.vstack((hh1, hh1_))

    xx2 = np.hstack((X[:,0:1], T[:,0:1]))   ## upstrm BC
    uu2 = u_exact[:,0:1]
    hh2 = h_exact[:,0:1]
    xx3 = np.hstack((X[:,-1:], T[:,-1:]))   ## dnstrm BC
    uu3 = u_exact[:,-1:]
    hh3 = h_exact[:,-1:]

    X_h_IC = xx1
    h_IC = hh1
    X_u_BC = np.vstack([xx2, xx3])
    X_h_BC = np.vstack([xx2, xx3])
    u_BC = np.vstack([uu2, uu3])
    h_BC = np.vstack([hh2, hh3])

    useObs = True
    ## obs velocity
    ind_obs_u = [16, 32]
    t_obs_u = np.array([])
    x_obs_u = np.array([])
    u_obs = np.array([])
    for iobs in ind_obs_u:
        t_obs_u = np.append( t_obs_u, t.flatten() )
        x_obs_u = np.append( x_obs_u, np.ones(Nt_train)*x[iobs] )
        u_obs = np.append( u_obs, add_noise(u_exact[:Nt_train, iobs]) )
    X_u_obs = np.vstack([x_obs_u, t_obs_u]).T
    u_obs = u_obs[:,None]
    ## obs water depth
    ind_obs_h = [16, 32]
    t_obs_h = np.array([])
    x_obs_h = np.array([])
    h_obs = np.array([])
    for iobs in ind_obs_h:
        t_obs_h = np.append( t_obs_h, t.flatten() )
        x_obs_h = np.append( x_obs_h, np.ones(Nt_train)*x[iobs] )
        h_obs = np.append( h_obs, add_noise(h_exact[:Nt_train, iobs]) )
    X_h_obs = np.vstack([x_obs_h, t_obs_h]).T
    h_obs = h_obs[:,None]

    X_f_train = X_star
    slope = np.hstack([np.array(slope) for _ in range(Nt_train)])[:,None]


    exist_mode = 2
    saved_path = 'saved_model/case4/PINN_SVE.pickle'
    weight_path = 'saved_model/case4/weights.out'
    # Training
    model1 = SVE_standard(X_h_IC,
                X_u_BC, X_h_BC,
                X_u_obs, X_h_obs,
                X_f_train,
                h_IC,
                u_BC, h_BC,
                u_obs,h_obs,
                layers,
                lb, ub, slope, b,
                X_star, u_star, h_star,
                ExistModel=exist_mode, uhDir=saved_path, wDir=weight_path,
                useObs=True)

    saved_path = 'saved_model/case4_mff/PINN_SVE.pickle'
    weight_path = 'saved_model/case4_mff/weights.out'
    wmff_path = 'saved_model/case4_mff/w_mff.out'
    # Training
    model2 = SVE_mff(X_h_IC,
                X_u_BC, X_h_BC,
                X_u_obs, X_h_obs,
                X_f_train,
                h_IC,
                u_BC, h_BC,
                u_obs,h_obs,
                layers, 
                lb, ub, slope, b,
                X_star, u_star, h_star,
                ExistModel=exist_mode, uhDir=saved_path, wDir=weight_path, wmffDir=wmff_path,
                useObs=True) 
    
    # Test data
    Nt_test = Nt_train
    N_test = Nt_test * Nx    ## Nt_test x Nx
    X_test = X_star[:N_test,:]
    x_test = X_test[:,0:1]
    t_test = X_test[:,1:2]
    u_test = u_star[:N_test,:]
    h_test = h_star[:N_test,:]

    # Prediction
    u_pred1, h_pred1 = model1.predict(x_test, t_test)
    error_h1 = np.linalg.norm(h_test-h_pred1,2)/np.linalg.norm(h_test,2)
    print('Error h (standard): %e' % (error_h1))
    
    rmse_h1 = np.sqrt(((h_test - h_pred1) ** 2).mean())
    print('RMSE h (standard): %.3f m' % rmse_h1 )

    u_pred2, h_pred2 = model2.predict(x_test, t_test)
    error_h2 = np.linalg.norm(h_test-h_pred2,2)/np.linalg.norm(h_test,2)
    print('Error h (ff): %e' % (error_h2))

    rmse_h2 = np.sqrt(((h_test - h_pred2) ** 2).mean())
    print('RMSE h (ff): %.3f m' % rmse_h2 )

    u_pred1 = u_pred1.reshape([Nt_test, Nx])
    h_pred1 = h_pred1.reshape([Nt_test, Nx])
    u_pred2 = u_pred2.reshape([Nt_test, Nx])
    h_pred2 = h_pred2.reshape([Nt_test, Nx])
    u_test = u_test.reshape([Nt_test, Nx])
    h_test = h_test.reshape([Nt_test, Nx])
    
    hours = np.arange(len(time_model[:Nt_test]))
    x = x[::-1]  ## reverse the channel for plotting
    xx, tt = np.meshgrid(x.flatten(), hours)

    factor = 0.3048   # ft to m 
    xx *= factor
    x  *= factor
    u_test *= factor
    u_pred1 *= factor
    u_pred2 *= factor
    h_test *= factor
    h_pred1 *= factor
    h_pred2 *= factor
    eles *= factor

    plt.rcParams.update({'font.size': 16})
    labels = ['(a)', '(b)', '(c)', '(d)', '(e)', '(f)']
    fig = plt.figure(figsize=(10.5, 8))
    gs = gridspec.GridSpec(3, 4, hspace=0.08, wspace=0.15)

    levels = np.linspace(0, 5.4, 10)
    ax0 = fig.add_subplot(gs[0, 1:3])
    cs = ax0.contourf(xx, tt, h_test[:Nt_test,:], cmap='rainbow', levels=levels, alpha = 0.8)
    ax0.scatter(X_u_BC[:,0][::6]*factor, X_u_BC[:,1][::6], marker='o', c='g', s=12, clip_on=False) # BC
    ax0.scatter(x_obs_h[::6]*factor, t_obs_h[::6], facecolors='none', edgecolors='k', marker='o', s=15, clip_on=False) # obs
    ax0.scatter(X_h_IC[:,0]*factor, X_h_IC[:,1], marker='*', c='r', s=25, clip_on=False) # snapshots
    ax0.set_ylabel('Time (h)')
    ax0.set_xticklabels([])
    ax0.text(0.05, 0.9, '{} Ref'.format(labels[0]), fontsize=16, transform=ax0.transAxes)
    divider = make_axes_locatable(ax0)
    cax = divider.append_axes("right", size="2%", pad=0.05)
    cb = fig.colorbar(cs, cax=cax, orientation='vertical')
    cb.ax.tick_params(labelsize=14)
    cb.ax.yaxis.offsetText.set_fontsize(14)
    cb.set_label('Water depth (m)', fontsize=14)

    tlist = [65, 80, 155, 160]
    xlist = [5, 5, 5, 5]
    ax0.scatter(xlist, tlist, marker='<', c='k', s=15, clip_on=False)


    ax1 = fig.add_subplot(gs[1, :2])
    cs = ax1.contourf(xx, tt, h_pred1[:Nt_test,:], cmap='rainbow', levels=levels, alpha = 0.8)
    ax1.set_ylabel('Time (h)')
    ax1.set_xticklabels([])
    ax1.text(0.05, 0.9, '{} PINN (standard)'.format(labels[1]), fontsize=16, transform=ax1.transAxes)
    
    ax2 = fig.add_subplot(gs[1, 2:])
    cs = ax2.contourf(xx, tt, h_pred2[:Nt_test,:], cmap='rainbow', levels=levels, alpha = 0.8)
    ax2.set_xticklabels([])
    ax2.set_yticklabels([])
    ax2.text(0.05, 0.9, '{} PINN (ff)'.format(labels[2]), fontsize=16, transform=ax2.transAxes)
    divider = make_axes_locatable(ax2)
    cax = divider.append_axes("right", size="2%", pad=0.05)
    cb = fig.colorbar(cs, cax=cax, orientation='vertical')
    cb.ax.tick_params(labelsize=14)
    cb.ax.yaxis.offsetText.set_fontsize(14)
    cb.set_label('Water depth (m)', fontsize=14)

    levels_error = np.linspace(-0.5, 0.5, 11)
    error1 = h_pred1[:Nt_test,:]-h_test[:Nt_test,:]
    ax3 = fig.add_subplot(gs[2, :2])
    cs = ax3.contourf(xx, tt, error1, levels=levels_error, cmap='bwr', alpha = 0.8, extend="both")
    ax3.set_xlabel('Distance upstream (m)')
    ax3.set_ylabel('Time (h)')
    ax3.text(0.05, 0.9, '{} PINN (standard)-Ref'.format(labels[3]), fontsize=16, transform=ax3.transAxes)

    ax4 = fig.add_subplot(gs[2, 2:])
    error2 = h_pred2[:Nt_test,:]-h_test[:Nt_test,:]
    cs = ax4.contourf(xx, tt, error2, levels=levels_error, cmap='bwr', alpha = 0.8, extend="both")
    ax4.set_xlabel('Distance upstream (m)')
    ax4.set_yticklabels([])
    ax4.text(0.05, 0.9, '{} PINN (ff)-Ref'.format(labels[4]), fontsize=16, transform=ax4.transAxes) 
    divider = make_axes_locatable(ax4)
    cax = divider.append_axes("right", size="2%", pad=0.05)
    cb = fig.colorbar(cs, cax=cax, orientation='vertical')
    cb.ax.tick_params(labelsize=14)
    cb.ax.yaxis.offsetText.set_fontsize(14)
    cb.set_label('Error (m)', fontsize=14) 

    #axes = axes.ravel()
    #for i in range(len(axes)):
    #    axes[i].text(0.05, 0.9, '{}'.format(labels[i]), fontsize=16, transform=axes[i].transAxes)

    #plt.tight_layout()
    plt.subplots_adjust(left=0.1,
                        bottom=0.08,
                        right=0.9,
                        top=0.97,
                        wspace=0.15,
                        hspace=0.08)
    plt.savefig('figures/case4/contour.pdf')
    plt.close()

    #tlist = [20, 50, 100, 200]
    tlist = [65, 80, 155, 160]
    plt.rcParams.update({'font.size': 15})
    fig, axes = plt.subplots( 2, 2, figsize=(15, 8), sharex=True, sharey=True)
    axes = axes.ravel() 
    for k in range(len(tlist)):
        axes[k].plot(x, h_test[tlist[k],:]+eles, 'ok', label='reference')
        axes[k].plot(x, h_pred1[tlist[k],:]+eles, '-b', linewidth=2, label='PINN (stardard)')
        axes[k].plot(x, h_pred2[tlist[k],:]+eles, '-r', linewidth=2, label='PINN (ff)')
        axes[k].fill_between(x.flatten(), eles, color='0.7')
        axes[k].text(0.8, 0.925, '{} t={} h'.format(labels[k], int(tlist[k])), fontsize=16, transform=axes[k].transAxes)
        if k in [0, 2]:
            axes[k].set_ylabel('Water stage (m)')
        axes[k].set_ylim([0.5,6])
        axes[k].grid()
        if k in [2, 3]:
            axes[k].set_xlabel('Distance upstream (m)')
        if k == 0:
            axes[k].legend(loc=2,prop={'size': 14})

    plt.tight_layout()
    plt.savefig('figures/case4/along_channel.pdf')
    plt.close()

