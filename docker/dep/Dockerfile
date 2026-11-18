FROM ubuntu:24.04
LABEL maintainer="felix.ruess@gmail.com"

# Add Tini
ENV TINI_VERSION v0.8.4
ADD https://github.com/krallin/tini/releases/download/${TINI_VERSION}/tini /tini
RUN chmod +x /tini

# setup environment
ENV LANG C.UTF-8

ARG DEBIAN_FRONTEND=noninteractive

# add Paparazzi PPA
RUN apt-get update && apt-get install -y software-properties-common
RUN add-apt-repository ppa:paparazzi-uav/ppa
#RUN add-apt-repository ppa:team-gcc-arm-embedded/ppa

# setup tzdata
ENV TZ=Etc/UTC
RUN ln -fs /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone
RUN apt-get -y install tzdata

# dependencies of the OCaml-free Paparazzi (Python ground segment), PprzGCS,
# JSBSim and the ARM cross compiler (Ubuntu 24.04: arm-none-eabi GCC 13.2,
# known to build the airborne code; GCC 14 needs the gvf.c fix)
RUN apt-get update && apt-get install -y \
    pprzgcs paparazzi-jsbsim ivy-c-dev ivy-python dfu-util \
    make cmake gcc g++ git bzip2 bc pkg-config libgsl-dev libusb-dev libpcre2-dev libxml2-dev libglib2.0-dev \
    python-is-python3 python3-lxml python3-numpy python3-yaml python3-unidecode python3-serial \
    python3-pyqt5 python3-pyqt5.qtsvg python3-pyqtgraph libsdl2-2.0-0 speech-dispatcher \
    gcc-arm-none-eabi libnewlib-arm-none-eabi gdb-multiarch \
    x11-apps curl \
    && rm -rf /var/lib/apt/lists/*

ENTRYPOINT ["/tini", "--"]
CMD ["bash"]
