
FROM continuumio/miniconda2

RUN mkdir /home/gnn
WORKDIR /home/gnn
COPY environment.yml .
COPY gae .

# Install deps for compiling m2m
RUN apt update && apt install -y build-essential git vim python2-dev 
RUN ldconfig

# Create conda environment:
# RUN conda env create -f /home/gnn/environment.yml
# ENV PATH /opt/conda/envs/gae/bin:$PATH
# ENV CONDA_DEFAULT_ENV gae
# RUN /bin/bash -c "source activate gae" 

# conda create -n gae python=2.7 tensorflow=1 google-auth networkx scikit-learn scipy numpy pandas geopandas jupyter -y

# Update and install libs
# RUN pip install google-auth && pip install -e . 
