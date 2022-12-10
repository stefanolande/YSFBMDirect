# pYSFBMDirect

pYSFBMDirect is a software that allows you to access Brandmeister as if it were a YSF reflector.
It works by using the YSF Direct protocol that is being currently experimented.

The goal of this software is to build a YSF repeater that can connect to Brandmeister Talk Groups, 
without losing the possibility to connect to other YSF rooms.

By adding pYSFBMDirect to your `YSFHosts.txt` file, you can connect to it through WIRES-X.
Then, simply change the TX DG-ID of your radio to change the DMR Talk Group.

![Network diagram](./images/diagram.svg)


## Installation

Clone the repository and create your configuration file. Set your callsign and password in the file.

```
git clone https://github.com/stefanolande/pYSFBMDirect.git
cp pYSFBMDirect.conf.example pYSFBMDirect.conf
```

Install the service 

```
mv pYSFBMDirect /opt/
cd /opt/pYSFBMDirect/
cp pYSFBMDirect.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable pYSFBMDirect.service
sudo systemctl start pYSFBMDirect.service
```

Add pYSFBMDirect as a YSF reflector

```
echo "01234;YSF-BM;YSF-BM;127.0.0.1;42002;001" >> /root/YSFHosts.txt
/usr/local/sbin/HostFilesUpdate.sh
```

`01234` is the YSF Room code, `YSF-BM` is the room name and can be changed as you wish (use a free room number).

`42002` is the port used to communicate with pYSFBMDirect. You can change it in the configuration file (`ysf_port`).

## Configuration of available Talk Groups

Each line of  the section `[DGID-TO-TG]` of `pYSFBMDirect.conf` is the format `DG-ID: TalkGroup`.
Example: `31: 2231` sets the DG-ID 31 to set the gateway to the Talk Group 2231.

You can add as many mapping as you wish with the 99 available DG-IDs (00-99). 
Pay attention that you cannot repeat DG-IDs and you cannot assign the same TG to multiple DG-IDs.
