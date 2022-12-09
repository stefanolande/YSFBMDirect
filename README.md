# pYSFBMGateway

pYSFBMGateway is a software that allows you to access Brandmeister as if it were a YSF reflector.
It works by using the YSF Direct protocol that is being currently experimented.

By adding pYSFBMGateway to your `YSFHosts.txt` file, you can connect to it through WIRES-X.
Then, simply change the TX DG-ID of your radio to change the DMR Talk Group.

![Network diagram](./images/diagram.svg)


## Installation

Clone the repository and create your configuration file and set your callsign and password.

```
git clone https://github.com/stefanolande/pYSFBMGateway.git
cp pYSFBMGateway.conf.example pYSFBMGateway.conf
```

Install the service 

```
mv pYSFBMGateway /opt/
cd /opt/pYSFBMGateway/
cp pYSFBMGateway.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable pYSFBMGateway.service
sudo systemctl start pYSFBMGateway.service
```

Add pYSFBMGateway as a YSF reflector

```
echo "01234;YSF-BM;YSF-BM;127.0.0.1;42002;001" >> /usr/local/etc/YSFHosts.txt
sudo systemctl restart ysfgateway
```

`01234` is the YSF Room code, `YSF-BM` is the room name and can be changed as you wish (use a free room number).

`42002` is the port used to communicate with pYSFBMGateway. You can change it in the configuration file (`ysf_port`).

## Configuration of available Talk Groups

Each line of  the section `[DGID-TO-TG]` of `pYSFBMGateway.conf` is the format `DG-ID: TalkGroup`.
Example: `31: 2231` sets the DG-ID 31 to set the gateway to the Talk Group 2231.

You can add as many mapping as you wish with the 99 available DG-IDs (00-99). 
Pay attention that you cannot repeat DG-IDs and you cannot assign the same TG to multiple DG-IDs.