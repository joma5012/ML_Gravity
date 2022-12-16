import os
import copy
import time
import numpy as np
from numpy.random.mtrand import laplace
import pandas as pd
import tensorflow as tf

from GravNN.Networks import utils
from GravNN.Networks.Constraints import *
from GravNN.Networks.Annealing import update_constant
from GravNN.Networks.Networks import load_network
from GravNN.Networks.Losses import compute_rms_components, compute_percent_error
import GravNN

np.random.seed(1234)


class PINNGravityModel(tf.keras.Model):
    # Initialize the class
    def __init__(self, config, network=None):
        """Custom Keras model that encapsulates the actual PINN as well as other relevant
        configuration information, and helper functions. This includes all
        training loops, methods to get all (or specific) outputs from the network, and additional
        optimization methods.

        Args:
            config (dict): hyperparameters and configuration variables needed to initialize the network.
            Consult the Config dictionaries within Network.Configs to get an idea of what options are currently
            implemented.
            network (keras.Model): the actual network that will be trained.
        """
        self.variable_cast = config.get("dtype", [tf.float32])[0]
        super(PINNGravityModel, self).__init__(dtype=self.variable_cast)
        self.config = config
        if network is None:
            self.network = load_network(config)
        else:
            self.network = network
        self.mixed_precision = tf.constant(
            self.config["mixed_precision"][0], dtype=tf.bool
        )
        
        self.calc_adaptive_constant = utils._get_annealing_fcn(config["lr_anneal"][0])
        self.loss_fcn = utils._get_loss_fcn(config['loss_fcn'][0])
        PINN_variables = utils._get_PI_constraint(config["PINN_constraint_fcn"][0])
        self.eval = PINN_variables[0]
        self.scale_loss = PINN_variables[1]
        self.adaptive_constant = tf.Variable(PINN_variables[2], dtype=self.variable_cast)
        self.beta = tf.Variable(self.config.get('beta', [0.0])[0], dtype=self.variable_cast)

        self.is_pinn = tf.cast(self.config["PINN_constraint_fcn"][0] != no_pinn, tf.bool)
        self.is_modified_potential = tf.cast(self.config["PINN_constraint_fcn"][0] == pinn_A_Ur, tf.bool)
        
        # jacobian ops (needed in LC loss terms) incompatible with XLA
        if ("L" in self.eval.__name__) or \
           ("C" in self.eval.__name__) or \
           (config['init_file'][0] is not None) or \
           (not self.config['jit_compile'][0]):
            self.train_step = self.wrap_train_step_njit
            self.test_step = self.wrap_test_step_njit
        else:
            self.train_step = self.wrap_train_step_jit
            self.test_step = self.wrap_test_step_jit

    def call(self, x, training=None):
        return self.eval(self.network, x, training)



    def train_step_fcn(self, data):
        """Method to train the PINN. First computes the loss components which may contain dU, da, dL, dC or some combination of these variables. These component losses are then scaled by the adaptive learning rate (if flag is True), 
        summed, scaled again (if using mixed precision), the adaptive learning rate is then updated, and then backpropagation
        occurs.

        Args:
            data (tf.Dataset): training data

        Returns:
            dict: dictionary of metrics passed to the callback.
        """
 
        x, y = data
        with tf.GradientTape(persistent=True) as tape:
            y_hat = self(x, training=True)

            rms_components = compute_rms_components(y_hat, y)
            percent_components = compute_percent_error(y_hat, y)
            
            updated_rms_components = self.scale_loss(
                tf.reduce_mean(rms_components,0), self.adaptive_constant
            )

            loss = self.loss_fcn(rms_components, percent_components)
            loss = self.optimizer.get_scaled_loss(loss)

        # calculate new adaptive constant
        adaptive_constant = self.calc_adaptive_constant(
            tape,
            updated_rms_components,
            self.adaptive_constant,
            self.beta,
            self.trainable_weights,
        )

        # # These lines are needed if using the gradient callback.
        # grad_comp_list = []
        # for loss_comp in updated_loss_components:
        #     gradient_components = tape.gradient(loss_comp, self.network.trainable_variables)
        #     grad_comp_list.append(gradient_components)

        gradients = tape.gradient(loss, self.network.trainable_variables)
        gradients = self.optimizer.get_unscaled_gradients(gradients)
        del tape

        # The PINN loss doesn't depend on the network's final layer bias, so the gradient is None and throws a warning
        # a = df/dx = d/dx stuff * (W_final x + b_final) = d stuff/dx * w
        # loss = a - a_hat
        # d loss/ d weights = no b_final
        self.optimizer.apply_gradients([
            (grad, var) for (grad, var) in zip(gradients, self.network.trainable_variables) if grad is not None
            ])
        return {
            "loss": loss,
            "percent_mean": tf.reduce_mean(percent_components),
            "percent_max": tf.reduce_max(percent_components),
            "loss_components" : rms_components,
            "percent_components" : percent_components
           # "adaptive_constant": adaptive_constant,
        }  # 'grads' : grad_comp_list}

    def test_step_fcn(self, data):
        x, y = data
        y_hat = self(x, training=True)

        rms_components = compute_rms_components(y_hat, y)
        percent_components = compute_percent_error(y_hat, y)
        updated_rms_components = self.scale_loss(
            tf.reduce_mean(rms_components,0), self.adaptive_constant
        )
        loss = self.loss_fcn(rms_components, percent_components)
        return {"loss": loss, 
                "percent_mean": tf.reduce_mean(percent_components),
                "percent_max": tf.reduce_max(percent_components),
                "loss_components" : rms_components,
                "percent_components" : percent_components
                }

    # JIT wrappers
    @tf.function(jit_compile=True)
    def wrap_train_step_jit(self, data):
        return self.train_step_fcn(data)
    
    @tf.function(jit_compile=False, experimental_relax_shapes=True)
    def wrap_train_step_njit(self, data):
        return self.train_step_fcn(data)
    
    @tf.function(jit_compile=True)
    def wrap_test_step_jit(self, data):
        return self.test_step_fcn(data)

    @tf.function(jit_compile=False, experimental_relax_shapes=True)
    def wrap_test_step_njit(self, data):
        return self.test_step_fcn(data)


    # API calls 
    def generate_nn_data(
        self,
        x,
    ):
        """Method responsible for generating all possible outputs of the
        PINN gravity model (U, a, L, C). Note that this is an expensive
        calculation due to the second order derivatives.

        TODO: Investigate if this method can be jit complied and be compatible
        with tf.Datasets for increased speed.

        Args:
            x (np.array): Input data (position)

        Returns:
            dict: dictionary containing all input and outputs of the network
        """
        x = copy.deepcopy(x)
        x_transformer = self.config["x_transformer"][0]
        a_transformer = self.config["a_transformer"][0]
        u_transformer = self.config["u_transformer"][0]
        x = x_transformer.transform(x)

        # This is a cumbersome operation as it computes the Hessian for each term
        u_pred, a_pred, laplace_pred, curl_pred = self.__nn_output((x, x))

        x_pred = x_transformer.inverse_transform(x)
        u_pred = u_transformer.inverse_transform(u_pred)
        a_pred = a_transformer.inverse_transform(a_pred)

        # TODO: (07/02/21): It's likely that laplace and curl should also be inverse transformed as well
        return {
            "x": x_pred,
            "u": u_pred,
            "a": a_pred,
            "laplace": laplace_pred,
            "curl": curl_pred,
        }

    @tf.function()
    def generate_potential_tf(self, x):
        x_preprocessor = getattr(self, 'x_preprocessor')
        u_postprocessor = getattr(self, 'u_postprocessor')
        x_network_input = x_preprocessor(x) 
        u_network_output = self.network(x_network_input)
        u_output = u_postprocessor(u_network_output)
        return u_output

    def generate_potential(self, x):
        """Method responsible for returning just the PINN potential.
        Use this method if a lightweight TF execution is desired

        Args:
            x (np.array): Input non-normalized position data (cartesian)

        Returns:
            np.array : PINN generated potential
        """
        x = copy.deepcopy(x)
        x_transformer = self.config["x_transformer"][0]
        u_transformer = self.config["u_transformer"][0]
        x = x_transformer.transform(x)
        u_pred = self.network(x)
        try:
            u_pred = u_transformer.inverse_transform(u_pred)
        except:
            u3_vec = np.zeros(x.shape)
            u3_vec[:] = u_pred
            u_pred = u_transformer.inverse_transform(u3_vec)[:,0]
        return u_pred

    #@tf.function(jit_compile=True)
    def generate_acceleration(self, x, batch_size=131072):
        """Method responsible for returning the acceleration from the
        PINN gravity model. Use this if a lightweight TF execution is
        desired and other outputs are not required.

        Args:
            x (np.array): Input non-normalized position data (cartesian)

        Returns:
            np.array: PINN generated acceleration
        """
        x_transformer = self.config["x_transformer"][0]
        a_transformer = self.config["a_transformer"][0]
        x = x_transformer.transform(x)

        x = tf.constant(x, dtype=self.variable_cast)
        
        # data = utils.chunks(x, 131072//2)

        if self.is_pinn:
            a_pred = self._pinn_acceleration_output(x)
        else:
            a_pred = self._nn_acceleration_output(x)
        a_pred = a_transformer.inverse_transform(a_pred)
        return a_pred

    def generate_dU_dxdx(self, x, batch_size=131072):
        """Method responsible for returning the acceleration from the
        PINN gravity model. Use this if a lightweight TF execution is
        desired and other outputs are not required.

        Args:
            x (np.array): Input non-normalized position data (cartesian)

        Returns:
            np.array: PINN generated acceleration
        """
        x_transformer = self.config["x_transformer"][0]
        a_transformer = self.config["a_transformer"][0]
        u_transformer = self.config["u_transformer"][0]
        x = x_transformer.transform(x)

        x = tf.constant(x, dtype=self.variable_cast)
        
        # data = utils.chunks(x, 131072//2)

        if self.is_pinn:
            jacobian = self._pinn_acceleration_jacobian(x)
        else:
            jacobian = self._nn_acceleration_jacobian(x)

        l_star = 1/x_transformer.scale_

        # a_transformer.scale_ = (1 / (l_star / t_star**2))
        # l_star/t_star**2 = 1/a_transformer.scale_
        # t_star**2/l_star = a_transformer.scale_
        # t_star = np.sqrt(a_transformer.scale_*l_star)

        t_star = np.sqrt(a_transformer.scale_*l_star)
        jacobian /= t_star**2
        # x_scale = x_transformer.scale_
        # u_scale = u_transformer.scale_
        # scale = x_scale**2/u_scale
        # jacobian = jacobian*scale
        return jacobian


    # private functions
    def __nn_output(self, dataset):
        x, y = dataset
        x = tf.Variable(x, dtype=self.variable_cast)
        assert self.config["PINN_constraint_fcn"][0] != no_pinn
        with tf.GradientTape(persistent=True) as g1:
            g1.watch(x)
            with tf.GradientTape() as g2:
                g2.watch(x)
                u = self.network(x)  # shape = (k,) #! evaluate network
            u_x = g2.gradient(u, x)  # shape = (k,n) #! Calculate first derivative
        u_xx = g1.batch_jacobian(u_x, x)

        laplacian = tf.reduce_sum(tf.linalg.diag_part(u_xx), 1, keepdims=True)

        curl_x = tf.math.subtract(u_xx[:, 2, 1], u_xx[:, 1, 2])
        curl_y = tf.math.subtract(u_xx[:, 0, 2], u_xx[:, 2, 0])
        curl_z = tf.math.subtract(u_xx[:, 1, 0], u_xx[:, 0, 1])

        curl = tf.stack([curl_x, curl_y, curl_z], axis=1)
        return u, -u_x, laplacian, curl

    @tf.function(jit_compile=True)
    def _nn_acceleration_output(self, x):
        a = self.network(x) 
        return a
    
    @tf.function()
    def _pinn_acceleration_output(self, x):
        if self.is_modified_potential:
            a = pinn_A_Ur(self.network, x, training=False)
        else:
            a = pinn_A(self.network, x, training=False)
        return a

    @tf.function(experimental_relax_shapes=True)
    def _pinn_acceleration_jacobian(self, x):
        with tf.GradientTape() as g1:
            g1.watch(x)
            with tf.GradientTape() as g2:
                g2.watch(x)
                u = self.network(x)  # shape = (k,) #! evaluate network
            a = -g2.gradient(u, x)  # shape = (k,n) #! Calculate first derivative
        jacobian = g1.batch_jacobian(a,x)
        return jacobian
        
    @tf.function(experimental_relax_shapes=True)
    def _nn_acceleration_jacobian(self,x):
        with tf.GradientTape() as g2:
            g2.watch(x)
            a = self.network(x)  # shape = (k,) #! evaluate network
        jacobian = g2.batch_jacobian(a, x)  # shape = (k,n) #! Calculate first derivative
        return jacobian



    # https://pychao.com/2019/11/02/optimize-tensorflow-keras-models-with-l-bfgs-from-tensorflow-probability/
    def optimize(self, dataset):
        """L-BFGS optimizer proposed in original PINN paper, but compatable with TF >2.0. Significantly slower
        than adam, and recommended only for fine tuning the networks after initial optimization with adam.

        Args:
            dataset (tf.Dataset): training input and output data

        """
        import tensorflow_probability as tfp

        class History:
            def __init__(self):
                self.history = []

        self.history = History()

        def function_factory(model, loss, train_x, train_y):
            """A factory to create a function required by tfp.optimizer.lbfgs_minimize.

            Args:
                model [in]: an instance of `tf.keras.Model` or its subclasses.
                loss [in]: a function with signature loss_value = loss(pred_y, true_y).
                train_x [in]: the input part of training data.
                train_y [in]: the output part of training data.

            Returns:
                A function that has a signature of:
                    loss_value, gradients = f(model_parameters).
            """

            # obtain the shapes of all trainable parameters in the model
            shapes = tf.shape_n(model.trainable_variables)
            n_tensors = len(shapes)

            # we'll use tf.dynamic_stitch and tf.dynamic_partition later, so we need to
            # prepare required information first
            count = 0
            idx = []  # stitch indices
            part = []  # partition indices

            for i, shape in enumerate(shapes):
                n = np.product(shape)
                idx.append(
                    tf.reshape(tf.range(count, count + n, dtype=tf.int32), shape)
                )
                part.extend([i] * n)
                count += n

            part = tf.constant(part)

            @tf.function  # (jit_compile=True)
            def assign_new_model_parameters(params_1d):
                """A function updating the model's parameters with a 1D tf.Tensor.

                Args:
                    params_1d [in]: a 1D tf.Tensor representing the model's trainable parameters.
                """

                params = tf.dynamic_partition(params_1d, part, n_tensors)
                for i, (shape, param) in enumerate(zip(shapes, params)):
                    model.trainable_variables[i].assign(tf.reshape(param, shape))

            # now create a function that will be returned by this factory
            @tf.function  # (jit_compile=True)
            def f(params_1d):
                """A function that can be used by tfp.optimizer.lbfgs_minimize.

                This function is created by function_factory.

                Args:
                params_1d [in]: a 1D tf.Tensor.

                Returns:
                    A scalar loss and the gradients w.r.t. the `params_1d`.
                """

                # use GradientTape so that we can calculate the gradient of loss w.r.t. parameters
                with tf.GradientTape() as tape:
                    # update the parameters in the model
                    assign_new_model_parameters(params_1d)
                    # calculate the loss
                    U_dummy = tf.zeros_like(train_x[:, 0:1])

                    # U_dummy = tf.zeros((tf.divide(tf.size(train_x),tf.constant(3)),1))
                    pred_y = model(train_x, training=True)
                    loss_value = loss(pred_y, train_y)

                # calculate gradients and convert to 1D tf.Tensor
                grads = tape.gradient(loss_value, model.trainable_variables)
                grads = tf.dynamic_stitch(idx, grads)

                # print out iteration & loss
                f.iter.assign_add(1)
                tf.print("Iter:", f.iter, "loss:", loss_value)

                # store loss value so we can retrieve later
                tf.py_function(f.history.append, inp=[loss_value], Tout=[])

                return loss_value, grads

            # store these information as members so we can use them outside the scope
            f.iter = tf.Variable(0)
            f.idx = idx
            f.part = part
            f.shapes = shapes
            f.assign_new_model_parameters = assign_new_model_parameters
            f.history = []

            return f

        inps = np.concatenate([x for x, y in dataset], axis=0)
        outs = np.concatenate([y for x, y in dataset], axis=0)

        # prepare prediction model, loss function, and the function passed to L-BFGS solver

        loss_fun = tf.keras.losses.MeanSquaredError()
        func = function_factory(self, loss_fun, inps, outs)

        # convert initial model parameters to a 1D tf.Tensor
        init_params = tf.dynamic_stitch(func.idx, self.network.trainable_variables)

        # train the model with L-BFGS solver
        results = tfp.optimizer.lbfgs_minimize(
            value_and_gradients_function=func,
            initial_position=init_params,
            max_iterations=2000,
            tolerance=1e-12,
        )  # , parallel_iterations=4)

        func.assign_new_model_parameters(results.position)
        self.history.history = func.history

    def model_size_stats(self):
        """Method which computes the number of trainable variables in the model as well
        as the binary size of the saved network and adds it to the configuration dictionary.
        """
        size_stats = {
            "params": [count_nonzero_params(self.network)],
            "size": [utils.get_gzipped_model_size(self)],
        }
        self.config.update(size_stats)

    def prep_save(self):
        """Method responsible for timestamping the network, adding the training history to the configuration dictionary, and formatting other variables into the configuration dictionary.
        """
        timestamp = pd.Timestamp(time.time(), unit="s").round("ms").ctime()
        time_JD = pd.Timestamp(timestamp).to_julian_date()

        os.makedirs(self.save_dir, exist_ok=True)
        os.makedirs(f"{self.save_dir}/Dataframes/", exist_ok=True)
        os.makedirs(f"{self.save_dir}/Networks/", exist_ok=True)
        self.config["timetag"] = timestamp
        try:
            self.config["history"] = [self.history.history]
        except:
            pass
        self.config["id"] = [time_JD]

        # dataframe cannot take fcn objects so settle on the names and convert to fcn on load 
        activation_type = type(self.config["activation"][0])
        activation_string = self.config["activation"][0] if activation_type == str else self.config["activation"][0].__name__
        self.config["activation"] = [activation_string]
        try:
            self.config["optimizer"] = [self.config["optimizer"][0].__module__]
        except:
            pass

        self.config["PINN_constraint_fcn"] = [self.config["PINN_constraint_fcn"][0]]  # Can't have multiple args in each list
        self.model_size_stats()

    def save(self, df_file=None, custom_data_dir=None, history=None, transformers=None):
        """Add remaining training / model variables into the configuration dictionary, then
        save the config variables into its own pickled file, and potentially add it to an existing
        dataframe defined by `df_file`.

        Args:
            df_file (str or pd.Dataframe, optional): path to dataframe to which the config variables should
            be appended or the loaded dataframe itself. Defaults to None.
        """
        # add final entries to config dictionary
        #time.sleep(np.random.randint(0,5)) # Make the process sleep with hopes that it decreases the likelihood that two networks save at the same time TODO: make this a lock instead.

        try:
            self.history = history
            self.config["x_transformer"][0] = transformers["x"]
            self.config["u_transformer"][0] = transformers["u"]
            self.config["a_transformer"][0] = transformers["a"]
            self.config["a_bar_transformer"][0] = transformers["a_bar"]
        except:
            pass

        # Save network and config information
        
        # the default save / load directory is within the GravNN package. 
        self.save_dir = os.path.dirname(GravNN.__file__) + "/../Data"

        # can specify an alternative save / load directory
        if custom_data_dir is not None:
            self.save_dir = custom_data_dir 

        self.prep_save()

        # convert configuration info to dataframe
        config = dict(sorted(self.config.items(), key=lambda kv: kv[0]))
        df = pd.DataFrame().from_dict(config).set_index("timetag")

        # save network and config to unique network directory
        network_id = self.config['id'][0]
        network_dir = f"{self.save_dir}/Networks/{network_id}/"
        self.network.save(network_dir + "network")
        df.to_pickle(network_dir + "config.data")

        # save config to preexisting dataframe if requested
        if df_file is not None:
            utils.save_df_row(self.config, f"{self.save_dir}/Dataframes/{df_file}")


def backwards_compatibility(config):
    """Convert old configuration variables to their modern
    equivalents such that they can be imported and tested.

    Args:
        config (dict): old configuration dictionary

    Returns:
        dict: new configuration dictionary
    """
    if float(config["id"][0]) < 2459343.9948726853:
        try:
            if np.isnan(config["PINN_flag"][0]): # nan case
                config["PINN_constraint_fcn"] = [no_pinn]
        except:
            pass
    if float(config["id"][0]) < 2459322.587314815:
        if config["PINN_flag"][0] == "none":
            config["PINN_constraint_fcn"] = [no_pinn]
        elif config["PINN_flag"][0] == "gradient":
            config["PINN_constraint_fcn"] = [pinn_A]
        elif config["PINN_flag"][0] == "laplacian":
            config["PINN_constraint_fcn"] = [pinn_APL]
        elif config["PINN_flag"][0] == "conservative":
            config["PINN_constraint_fcn"] = [pinn_APLC]
    
        if "class_weight" not in config:
            config["class_weight"] = [1.0]

        if "dtype" not in config:
            config["dtype"] = [tf.float32]
    if float(config['id'][0]) < 2459640.439074074:
        config['loss_fcn'] = ['rms_summed']
    if float(config["id"][0]) < 2459628.436423611:
        # Before this date, it was assumed that data would be drawn with SH if planet, and 
        # Polyhedral if asteroid. This is no longer true. 
        if "Planets" in config["planet"][0].__module__:
            config["gravity_data_fcn"] = [GravNN.GravityModels.SphericalHarmonics.get_sh_data]
        else:
            config["gravity_data_fcn"] = [GravNN.GravityModels.Polyhedral.get_poly_data]

    if "eros200700.obj" in config["grav_file"][0]:
        from GravNN.CelestialBodies.Asteroids import Eros
        config['grav_file'] = [Eros().obj_200k]

    if "lr_anneal" not in config:
        config["lr_anneal"] = [False]

    if "mixed_precision" not in config:
        config["use_precision"] = [False]

    return config


def load_config_and_model(model_id, df_file, custom_data_dir=None):
    """Primary loading function for the networks and their
    configuration information.

    Args:
        model_id (float): the timestamp of the desired network to load
        df_file (str or pd.Dataframe): the path to (or dataframe itself) containing the network
        configuration parameters of interest.

    Returns:
        tuple: configuration/hyperparameter dictionary, compiled PINNGravityModel
    """
    data_dir = f"{os.path.dirname(GravNN.__file__)}/../Data/"
    if custom_data_dir is not None:
        data_dir = custom_data_dir

    # Get the configuration data specified model_id
    if type(df_file) == str:
        # If the config dataframe hasn't been loaded  
        df_file_path = f"{data_dir}/Dataframes/{df_file}"
        config = utils.get_df_row(model_id, df_file_path)
    else:
        # If the config dataframe has already been loaded
        config = df_file[model_id == df_file["id"]].to_dict()
        for key, value in config.items():
            config[key] = list(value.values())

    # Reinitialize the model
    config = backwards_compatibility(config)
    network = tf.keras.models.load_model(
        f"{data_dir}/Networks/{model_id}/network"
    )
    model = PINNGravityModel(config, network)
    optimizer = utils._get_optimizer(config["optimizer"][0])
    model.compile(optimizer=optimizer, loss="mse") 

    return config, model


def count_nonzero_params(model):
    params = 0
    for v in model.trainable_variables:
        params += tf.math.count_nonzero(v)
    return params.numpy()
