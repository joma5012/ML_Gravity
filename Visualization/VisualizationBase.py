from abc import ABC, abstractmethod
import matplotlib.pyplot as plt
import os
class VisualizationBase(ABC):
    file_directory = os.path.splitext(__file__)[0]  + "/../../Files/Plots/" 
    trajectory = None
    accelerations = None
    def __init__(self):
        plt.rc('text', usetex=True)
        plt.rc('font', family='serif')
        return

    def new3DFig(self):
        fig = plt.figure(num=None, figsize=(5, 3.5), dpi=200)
        ax = fig.add_subplot(111, projection='3d')
        ax.set_xlabel(r'$x$ (m)', fontsize=10)
        ax.set_ylabel(r'$y$ (m)', fontsize=10)
        ax.set_zlabel(r'$z$ (m)', fontsize=10)
        ax.xaxis.set_major_locator(plt.MaxNLocator(6))
        ax.yaxis.set_major_locator(plt.MaxNLocator(6))
        ax.zaxis.set_major_locator(plt.MaxNLocator(6))
        ax.tick_params(labelsize=8)
        ax.legend(prop={'size': 10})
        ax.grid(linestyle="--", linewidth=0.1, color='.25', zorder=-10)

        return fig, ax

    def newFig(self):
        fig = plt.figure(num=None, figsize=(5, 3.5), dpi=200)
        ax = fig.add_subplot(111)
        ax.tick_params(labelsize=8)
        #ax.legend(prop={'size': 10})
        ax.grid(linestyle="--", linewidth=0.1, color='.25', zorder=-10)
        return fig, ax

    def save(self, fig, name):
        #If the name is an absolute path -- save to the path
        if os.path.isabs(name):
            try:
                plt.savefig(name, bbox_inches='tight')
            except:
                print("Couldn't save " + name)
            return    
        
        # If not absolute, create a directory and save the plot
        if not os.path.exists(self.file_directory):
            os.makedirs(self.file_directory)
        try:
            plt.savefig(self.file_directory + name, bbox_inches='tight')
        except:
            print("Couldn't save " + name)

