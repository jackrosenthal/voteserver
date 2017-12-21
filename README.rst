voteserver
==========

This script runs a server (to be accessed using netcat) to run an interactive
poll. To use:

1. Create a file ``vote.yaml`` that specifies the voting options. See
   ``vote.yaml.sample`` for an example, or the source code for a complete
   specification of what goes in the file.
2. Run ``python3 voteserver.py PORT_NUMBER``
3. Connect using ``nc``!

To control the server once it is running, use the commands specified in
``voteserver.py``.
