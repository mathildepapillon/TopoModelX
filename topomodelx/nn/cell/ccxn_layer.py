"""Implementation of a simplified, convolutional version of CCXN layer from Hajij et. al: Cell Complex Neural Networks."""

import torch

from topomodelx.base.conv import Conv


class CCXNLayer(torch.nn.Module):
    """Layer of a Convolutional Cell Complex Network (CCXN).

    Implementation of a simplified version of the CCXN layer proposed in [1]_.

    This layer is composed of two convolutional layers:
    1. A convolutional layer sending messages from nodes to nodes.
    2. A convolutional layer sending messages from edges to faces.
    Optionally, attention mechanisms can be used.

    Notes
    -----
    This is the architecture proposed for entire complex classification.

    Parameters
    ----------
    in_channels_0 : int
        Dimension of input features on nodes (0-cells).
    in_channels_1 : int
        Dimension of input features on edges (1-cells).
    in_channels_2 : int
        Dimension of input features on faces (2-cells).
    att : bool, default=False
        Whether to use attention.

    References
    ----------
    .. [1] Hajij, Istvan, Zamzmi.
        Cell complex neural networks.
        Topological data analysis and beyond workshop at NeurIPS 2020.
        https://arxiv.org/pdf/2010.00743.pdf
    .. [2] Papillon, Sanborn, Hajij, Miolane.
        Equations of topological neural networks (2023).
        https://github.com/awesome-tnns/awesome-tnns/
    .. [3] Papillon, Sanborn, Hajij, Miolane.
        Architectures of topological deep learning: a survey on topological neural networks (2023).
        https://arxiv.org/abs/2304.10031.
    """

    def __init__(
        self, in_channels_0, in_channels_1, in_channels_2, att: bool = False
    ) -> None:
        super().__init__()
        self.conv_0_to_0 = Conv(
            in_channels=in_channels_0, out_channels=in_channels_0, att=att
        )
        self.conv_1_to_2 = Conv(
            in_channels=in_channels_1, out_channels=in_channels_2, att=att
        )

    def forward(self, x_0, x_1, neighborhood_0_to_0, neighborhood_1_to_2, x_2=None):
        r"""Forward pass.

        The forward pass was initially proposed in [1]_.
        Its equations are given in [2]_ and graphically illustrated in [3]_.

        The forward pass of this layer is composed of two steps.

        1. The convolution from nodes to nodes is given by an adjacency message passing scheme (AMPS):

        ..  math::
            \begin{align*}
            &🟥 \quad m_{y \rightarrow \{z\} \rightarrow x}^{(0 \rightarrow 1 \rightarrow 0)}
                = M_{\mathcal{L}_\uparrow}(h_x^{(0)}, h_y^{(0)}, \Theta^{(y \rightarrow x)})\\
            &🟧 \quad m_x^{(0 \rightarrow 1 \rightarrow 0)}
                = \text{AGG}_{y \in \mathcal{L}_\uparrow(x)}(m_{y \rightarrow \{z\} \rightarrow x}^{0 \rightarrow 1 \rightarrow 0})\\
            &🟩 \quad m_x^{(0)}
                = m_x^{(0 \rightarrow 1 \rightarrow 0)}\\
            &🟦 \quad h_x^{t+1,(0)}
                = U^{t}(h_x^{(0)}, m_x^{(0)})
            \end{align*}

        2. The convolution from edges to faces is given by cohomology message passing scheme, using the coboundary neighborhood:

        .. math::
            \begin{align*}
            &🟥 \quad m_{y \rightarrow x}^{(r' \rightarrow r)}
                = M^t_{\mathcal{C}}(h_{x}^{t,(r)}, h_y^{t,(r')}, x, y)\\
            &🟧 \quad m_x^{(r' \rightarrow r)}
                = \text{AGG}_{y \in \mathcal{C}(x)} m_{y \rightarrow x}^{(r' \rightarrow r)}\\
            &🟩 \quad m_x^{(r)}
                = m_x^{(r' \rightarrow r)}\\
            &🟦 \quad h_{x}^{t+1,(r)}
                = U^{t,(r)}(h_{x}^{t,(r)}, m_{x}^{(r)})
            \end{align*}

        Parameters
        ----------
        x_0 : torch.Tensor, shape=[n_0_cells, channels]
            Input features on the nodes of the cell complex.
        x_1 : torch.Tensor, shape=[n_1_cells, channels]
            Input features on the edges of the cell complex.
        neighborhood_0_to_0 : torch.sparse
            shape=[n_0_cells, n_0_cells]
            Neighborhood matrix mapping nodes to nodes (A_0_up).
        neighborhood_1_to_2 : torch.sparse
            shape=[n_2_cells, n_1_cells]
            Neighborhood matrix mapping edges to faces (B_2^T).
        x_2 : torch.Tensor, shape=[n_2_cells, channels]
            Input features on the faces of the cell complex.
            Optional, only required if attention is used between edges and faces.

        Returns
        -------
        torch.Tensor, shape=[1, num_classes]
            Output prediction on the entire cell complex.
        """
        x_0 = torch.nn.functional.relu(x_0)
        x_1 = torch.nn.functional.relu(x_1)

        x_0 = self.conv_0_to_0(x_0, neighborhood_0_to_0)
        x_0 = torch.nn.functional.relu(x_0)

        x_2 = self.conv_1_to_2(x_1, neighborhood_1_to_2, x_2)
        x_2 = torch.nn.functional.relu(x_2)

        return x_0, x_1, x_2
