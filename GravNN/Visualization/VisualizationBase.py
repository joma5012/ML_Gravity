import os
from abc import ABC, abstractmethod

import matplotlib as mpl
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D
import numpy as np

class VisualizationBase(ABC):

    def __init__(self, save_directory=None, halt_formatting=False):
        if save_directory is None:
            self.file_directory = os.path.abspath('.') +"/Plots/"
        else:
            self.file_directory = save_directory

        if not halt_formatting:
            plt.rc('text', usetex=True)
            plt.rc('font', family='serif')
            plt.rc('font', size= 11.0)
            mpl.rcParams['axes.prop_cycle'] = mpl.cycler(color=['blue', 'green', 'red', 'orange', 'gold',  'salmon',  'lime', 'magenta','lavender', 'yellow', 'black', 'lightblue','darkgreen', 'pink', 'brown',  'teal', 'coral',  'turquoise',  'tan', 'gold'])

         # ~ 5:3 aspect ratio
         # (tall, wide)
        golden_ratio = (5**.5 - 1) / 2
        self.tri_page = (2.1, 2.1*golden_ratio)
        self.half_page = (3, 3*golden_ratio) #
        self.full_page = (6.3, 6.3*golden_ratio) 

        # AAS textwidth is 6.5
        self.half_page = (3.25, 3.25*golden_ratio)

        silver_ratio = 1/(1 + np.sqrt(2))
        self.tri_vert_page = (6.3, 6.3*silver_ratio)

        self.fig_size = (5,3.5) #(3, 1.8) is half page. 
        return

    def new3DFig(self):
        fig = plt.figure(num=None, figsize=(5,3.5), dpi=200)
        ax = fig.add_subplot(111, projection='3d')
        ax.set_xlabel(r'$x$ (m)', fontsize=10)
        ax.set_ylabel(r'$y$ (m)', fontsize=10)
        ax.set_zlabel(r'$z$ (m)', fontsize=10)
        ax.xaxis.set_major_locator(plt.MaxNLocator(6))
        ax.yaxis.set_major_locator(plt.MaxNLocator(6))
        ax.zaxis.set_major_locator(plt.MaxNLocator(6))
        ax.tick_params(labelsize=10)
        ax.legend(prop={'size': 10})
        ax.get_legend().remove()
        ax.grid(linestyle="--", linewidth=0.1, color='.25', zorder=-10)

        return fig, ax

    def newFig(self, fig_size=None):
        if fig_size is None:
            fig_size = self.fig_size
        fig = plt.figure(num=None, figsize=fig_size, dpi=200)
        ax = fig.add_subplot(111)
        ax.tick_params(labelsize=10)
        #ax.legend(prop={'size': 10})
        ax.grid(which='both',linestyle="--", linewidth=0.1, color='.25', zorder=-10)
        return fig, ax

    def save(self, fig, name):
        #If the name is an absolute path -- save to the path
        if os.path.isabs(name):
            try:
                plt.figure(fig.number)
                plt.savefig(name, bbox_inches='tight')
            except:
                print("Couldn't save " + name)
            return    
        
        # If not absolute, create a directory and save the plot
        directory = os.path.abspath(os.path.dirname(self.file_directory + name))
        directory = directory.replace(".","_")
        directory = directory.replace("[", "_")
        directory = directory.replace("]", "_")
        directory = directory.replace(" ", "")
        directory = directory.replace(",", "_")

        filename =  os.path.basename(name)
        if not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)
        try:
            plt.savefig(directory+"/" + filename, bbox_inches='tight')
        except:
            print("Couldn't save " + filename)

