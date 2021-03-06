from keras import layers, models, Model
import tensorflow as tf
import tensorflow_probability as tfp

"""class AtariGRU(layers.Layer):
    def __init__(self, units, time_steps):
        super().__init__()
        self.time_steps = time_steps
        self.n_hidden_states = int(1.5*time_steps)
        self.steps = 0
        self.rnn = layers.GRU(units=units, return_sequences=True, stateful=True)
    
    def call(self, inputs):
        #shape: (1, time_steps, units)
        time_steps = inputs.shape[1]
        remaining_steps = self.n_hidden_states - self.steps

        if remaining_steps < time_steps:
            remaining_sequences = self.rnn(inputs[:,:remaining_steps,:])
            self.rnn.reset_states()
            sequences = self.rnn(inputs[:, remaining_steps:, :])
            sequences = tf.concat([remaining_sequences, sequences], axis = 1)            
        else:
            sequences = self.rnn(inputs)

        self.steps = (self.steps + time_steps) % self.n_hidden_states
        return sequences"""

class AtariGRU(layers.Layer):
    def __init__(self, units):
        super().__init__()
        self.steps = 0
        self.initial_cell = tf.zeros(shape=(units))
        self.rnn = layers.GRU(units=units, return_sequences=True, return_state=True)
        
    
    def call(self, inputs, dones, cell_states, training=True):
        #shape: (num_envs, batch_size, units)
        #dones: (num_envs, batch_size)

        if cell_states is None:
            cell_states = self.initial_cell
            num_envs = inputs.shape[0]
            cell_states = tf.expand_dims(cell_states, axis=0)
            cell_states = tf.repeat(cell_states, num_envs, axis=0)
        
        inputs = tf.split(inputs, inputs.shape[1], axis=1)
        sequences = []
        for x, done in zip(inputs, tf.transpose(dones)):
            indices = tf.where(done)
            if len(indices):
                initial_cells = tf.expand_dims(self.initial_cell, axis=0)
                initial_cells = tf.repeat(initial_cells, len(indices), axis=0)
                cell_states = tf.tensor_scatter_nd_update(cell_states, indices, initial_cells)
            hidden_state, cell_states = self.rnn(inputs=x, initial_state=cell_states, training=training)
            
            sequences.append(hidden_state)
        sequences = tf.concat(sequences, axis=1)
        return sequences, cell_states



class AtariNetwork(Model):
    def __init__(self):
        super().__init__()
        self.c1 = layers.Conv2D(filters=64, kernel_size=8, strides=4, activation='relu', input_shape=(105, 80, 12))
        self.c2 = layers.Conv2D(filters=128, kernel_size=4, strides=2, activation='relu', input_shape=(25, 19, 128))
        self.c3 = layers.Conv2D(filters=128, kernel_size=3, strides=1, activation='relu', input_shape=(11, 8, 128))
        self.fc1 = layers.Flatten()
        self.fc2 = layers.Dense(800, activation='relu')
        self.layer_norm = layers.LayerNormalization()
        self.cnn = models.Sequential([self.c1, self.c2, self.c3, self.fc1, self.fc2, self.layer_norm])
        self.gru = AtariGRU(units=800)
        self.concatenate = layers.Concatenate(axis=2)
    def call(self, inputs, dones, cell_states=None, training=True):
        #inputs must be of shape [num_envs, batch_size, observation_spec]  
        num_envs, batch_size = inputs.shape[:2]
        inputs = tf.reshape(inputs, shape=[num_envs*batch_size] + inputs.shape[2:])            
        x = self.cnn(inputs, training=training)
        x = tf.reshape(x, shape=(num_envs, batch_size, x.shape[-1]))
        sequences, cell_states = self.gru(x, dones, cell_states=cell_states, training=training)
        x = self.concatenate([x, sequences], training=training)
        return x, cell_states

class AtariActorCriticNetwork(Model):
    def __init__(self, encoder, n_actions):
        super().__init__()
        self.encoder = encoder
        self.actor = layers.Dense(units=n_actions)
        self.critic = layers.Dense(units=1)

    def call(self, inputs, dones, cell_states=None, action=None, training=True):
        x, new_cell_states = self.encoder(inputs, dones=dones, cell_states=cell_states, training=training)
        logits = self.actor(x, training=training)
        values = self.critic(x, training=training)
        values = tf.squeeze(values, axis=2)

        distribution = tfp.distributions.Categorical(logits=logits, dtype=tf.int64)
        if action is None:
            action = distribution.sample()
        return action, distribution.log_prob(action), values, distribution.entropy(), new_cell_states, 