#!/usr/bin/env bash

[ "$UID" -eq 0 ] || exec sudo bash "$0" "$@"

rm -rf /tmp/YSFBMDirect

cd /tmp/ || exit
git clone https://github.com/stefanolande/YSFBMDirect.git
cp YSFBMDirect/YSFBMDirect.conf.example YSFBMDirect/YSFBMDirect.conf


while [[ $call == '' ]]
do
    read -r -p "Enter your callsign: " call
done

while [[ $password == '' ]]
do
	read -r -p "Enter your Brandmeister password: " password
done

sed -i "s/CALLSIGN/$call/" YSFBMDirect/YSFBMDirect.conf
sed -i "s/PASSWORD/$password/" YSFBMDirect/YSFBMDirect.conf

read -r -p "Do you want to edit the Talk Group configuration [y/N]?" edit_tg

while [[ $edit_tg != 'y' ]] && [[ $edit_tg != 'Y' ]] && [[ $edit_tg != 'n' ]] && [[ $edit_tg != 'N' ]] && [[ $edit_tg != '' ]]
do
	read -r -p "Do you want to edit the Talk Group configuration [y/N]?" edit_tg
done

if [[ $edit_tg == 'y' ]] || [[ $edit_tg == 'Y' ]];
then
	editor YSFBMDirect/YSFBMDirect.conf
else
	echo "You can edit it later in /opt/YSFBMDirect/YSFBMDirect.conf"
fi

rm -rf /opt/YSFBMDirect
cp -r YSFBMDirect /opt/
cd /opt/YSFBMDirect/ || exit
cp ysfbmdirect.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable ysfbmdirect.service
sudo systemctl start ysfbmdirect.service

ysf="01234;YSF-BM;YSF-BM;127.0.0.1;42002;001"
if ! grep -q $ysf /root/YSFHosts.txt; then
	echo $ysf >> /root/YSFHosts.txt
	/usr/local/sbin/HostFilesUpdate.sh
fi