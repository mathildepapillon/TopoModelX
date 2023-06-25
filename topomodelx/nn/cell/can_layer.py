import torch
from torch.nn import Linear, Parameter
from torch import Tensor
from torch.nn import functional as F

from topomodelx.base.conv import MessagePassing
from topomodelx.base.aggregation import Aggregation


class CANMultiHeadAttention(MessagePassing):
    r"""Attentional Message Passing from Cell Attention Network (CAN). [CAN22]_

    Parameters
    ----------
    in_channels : int
        Number of input channels.
    out_channels : int
        Number of output channels.
    aggr_func : string
        Aggregation function to use. Options are "sum", "mean", "max".
    initialization : string
        Initialization method for the weights of the layer.

    Notes
    -----
    [] If there are no non-zero values in the neighborhood, then the neighborhood is empty. 

    References
    ----------
    [CAN22] Giusti, Battiloro, Testa, Di Lorenzo, Sardellitti and Barbarossa. “Cell attention networks”. In: arXiv preprint arXiv:2209.08179 (2022).
        paper: https://arxiv.org/pdf/2209.08179.pdf
    """

    def __init__(
        self,
        in_channels,
        out_channels,
        heads,
        concat,
        att_activation,
        aggr_func="sum",
        initialization="xavier_uniform",
    ):
        super().__init__(
            att=True,
            initialization=initialization,
            aggr_func=aggr_func,
        )

        assert att_activation in ["leaky_relu", "elu", "tanh"]

        self.in_channels = in_channels
        self.out_channels = out_channels
        self.att_activation = att_activation
        self.heads = heads
        self.concat = concat

        self.lin = torch.nn.Linear(in_channels, heads * out_channels, bias=False)
        self.att_weight_src = Parameter(
            torch.Tensor(
                1, heads, out_channels
            )
        )
        self.att_weight_dst = Parameter(
            torch.Tensor(
                1, heads, out_channels
            )
        )

        self.reset_parameters()

    def reset_parameters(self):
        r"""Reset the layer parameters."""
        torch.nn.init.xavier_uniform_(self.att_weight_src)
        torch.nn.init.xavier_uniform_(self.att_weight_dst)
        self.lin.reset_parameters()

    def forward(self, x_source, neighborhood):
        r"""Forward pass.

        Parameters
        ----------
        x_source : torch.Tensor, shape=[n_k_cells, channels]
            Input features on the k-cell of the cell complex.
        neighborhood : torch.sparse, shape=[n_k_cells, n_k_cells]
            Neighborhood matrix mapping k-cells to k-cells (A_k). [up, down]

        Returns
        -------
        out : torch.Tensor, shape=[n_k_cells, channels]
        """
        if not neighborhood.values().nonzero().size(0) > 0 and self.concat:
            return torch.zeros((x_source.shape[0], self.out_channels * self.heads), device=x_source.device)
        elif not neighborhood.values().nonzero().size(0) > 0 and not self.concat:
            return torch.zeros((x_source.shape[0], self.out_channels), device=x_source.device)

        # Compute the linear transformation on the source features
        x_message = self.lin(x_source).view(-1, self.heads, self.out_channels)

        # Compute the attention coefficients
        target_index_i, source_index_j = neighborhood.indices()

        x_source_per_message = x_message[source_index_j] # (E, H, C)
        x_target_per_message = x_message[target_index_i] # (E, H, C)

        alpha_src = (x_source_per_message * self.att_weight_src).sum(dim=-1) # (E, H)
        alpha_dst = (x_target_per_message * self.att_weight_dst).sum(dim=-1) # (E, H)

        # Apply activation function
        if self.att_activation == "elu":
            alpha_src, alpha_dst = torch.nn.functional.elu(alpha_src), \
                                    torch.nn.functional.elu(alpha_dst)
        elif self.att_activation == "leaky_relu":
            alpha_src, alpha_dst = torch.nn.functional.leaky_relu(alpha_src), \
                                        torch.nn.functional.leaky_relu(alpha_dst)
        elif self.att_activation == "tanh":
            alpha_src, alpha_dst = torch.nn.functional.tanh(alpha_src), \
                                    torch.nn.functional.tanh(alpha_dst)
        else:
            raise NotImplementedError(
                f"Activation function {self.att_activation} not implemented."
            )

        # TODO: for each head, updates the neighborhood with the attention coefficients
        neighborhood_values = neighborhood.values()
        alpha = alpha_src + alpha_dst
        updated_neighborhood = neighborhood_values[:,None] + alpha # Broadcasting addition

        # TODO: normalize the neighborhood for each head with the softmax function applied on rows of the neighborhood
        normalized_neighborhood = F.softmax(updated_neighborhood, dim=1) # (E, H)

        # TODO: for each head, Aggregate the messages
        message = x_source_per_message * normalized_neighborhood[:,:,None] # (E, H, C)
        out = torch.zeros((x_source.shape[0], self.heads, self.out_channels), device=x_source.device)
        out.index_add_(0, target_index_i, message)

        # TODO: if concat true, concatenate the messages for each head. Otherwise, average the messages for each head.
        if self.concat:
            out = out.view(-1, self.heads * self.out_channels)
        else:
            out = out.mean(dim=1)

        return out
    
class CANLayer(torch.nn.Module): 

    r"""Layer of the Cell Attention Network (CAN) model.

    The CAN layer considers an attention convolutional message passing though the upper and lower neighborhoods of the cell.

    ..  math::
        \mathcal N_k \in  \mathcal N = \{A_{\uparrow, r}, A_{\downarrow, r}\}

    ..  math::
        \begin{align*}            
        &🟥 \quad m_{y \rightarrow x}^{(r \rightarrow r)} = M_{\mathcal N_k}(h_x^{t}, h_y^{t}, \Theta^{t}_k)\\ 
        &🟧 \quad m_x^{(r \rightarrow r)} = \text{AGG}_{y \in \mathcal{N}_k(x)}(m_{y \rightarrow x}^{(r \rightarrow r)})\\
        &🟩 \quad m_x^{(r)} = \text{AGG}_{\mathcal{N}_k\in\mathcal N}m_x^{(r \rightarrow r)}\\            
        &🟦 \quad h_x^{t+1,(r)} = U^{t}(h_x^{t}, m_x^{(r)})
        \end{align*}

    Notes
    -----
    [] Add multi-head attention

    References
    ----------
    [CAN22] Giusti, Battiloro, Testa, Di Lorenzo, Sardellitti and Barbarossa. “Cell attention networks”. In: arXiv preprint arXiv:2209.08179 (2022).
        paper: https://arxiv.org/pdf/2209.08179.pdf

    Parameters
    ----------
    in_channels : int
        Dimension of input features on n-cells.
    out_channels : int
        Dimension of output
    skip_connection : bool, optional
        If True, skip connection is added, by default True
    att_activation : str, optional
        Activation function for the attention coefficients, by default "leaky_relu". ["elu", "leaky_relu", "tanh"]
    """

    def __init__(self, 
                in_channels: int,
                out_channels: int,
                heads: int = 1,
                concat: bool = True,
                skip_connection: bool = True,
                att_activation: str = "leaky_relu",
                aggr_func="sum",
                update_func: str = "relu",
                **kwargs):

        super().__init__()

        assert in_channels > 0, ValueError("Number of input channels must be > 0")
        assert out_channels > 0, ValueError("Number of output channels must be > 0")

        # lower attention
        self.lower_att = CANMultiHeadAttention(
            in_channels=in_channels, 
            out_channels=out_channels, 
            heads=heads, 
            att_activation=att_activation,
            concat=concat
        )

        # upper attention
        self.upper_att = CANMultiHeadAttention(
            in_channels=in_channels, 
            out_channels=out_channels, 
            heads=heads, 
            att_activation=att_activation,
            concat=concat
        )

        # linear transformation
        if skip_connection:
            out_channels = out_channels * heads if concat else out_channels
            self.lin = Linear(in_channels, out_channels, bias=False)
            self.eps = 1 + 1e-6

        # between-neighborhood aggregation and update
        self.aggregation = Aggregation(aggr_func=aggr_func, update_func=update_func)

        self.reset_parameters()

    def reset_parameters(self):
        r"""Reset the parameters of the layer."""
        self.lower_att.reset_parameters()
        self.upper_att.reset_parameters()
        if hasattr(self, "lin"):
            self.lin.reset_parameters()

    def forward(self, x, lower_neighborhood, upper_neighborhood) -> Tensor:

        r"""Forward pass.

        Parameters
        ----------
        x : torch.Tensor, shape=[n_k_cells, channels]
            Input features on the k-cell of the cell complex.
        lower_neighborhood : torch.sparse
            shape=[n_k_cells, n_k_cells]
            Lower neighborhood matrix mapping k-cells to k-cells (A_k_low).
        upper_neighborhood : torch.sparse
            shape=[n_k_cells, n_k_cells]
            Upper neighborhood matrix mapping k-cells to k-cells (A_k_up).

        Returns
        -------
        _ : torch.Tensor, shape=[n_k_cells, out_channels]
        """

        # message and within-neighborhood aggregation
        lower_x = self.lower_att(x, lower_neighborhood)
        upper_x = self.upper_att(x, upper_neighborhood)

        # skip connection
        if hasattr(self, "lin"):
            w_x = self.lin(x)*self.eps

        # between-neighborhood aggregation and update
        out = self.aggregation([lower_x, upper_x, w_x]) if hasattr(self, "lin") else self.aggregation([lower_x, upper_x])

        return out