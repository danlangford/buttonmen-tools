# buttonmen-tools

we frequently like to update button list as well as the button stats.

## [buttondata.json](public/buttondata.json)

this is pulled from the prod site. You must be authenticated (have a cookie).
If you inspect the [create_game](https://www.buttonweavers.com/ui/create_game.html) page
you will find a `POST` to `/api/responder` in which that data sent was `{"type":"loadButtonData","automatedApiCall":false}`. 
The response of this call makes up buttondata.json. 
I enhance that `message` field to include the date,
and we display this message on the ButtonFilter page. 

## [buttonunimpl.json](public/buttonunimpl.json)

This is pulled from the staging site.
Same method using the [create_game](https://staging.buttonweavers.com/ui/create_game.html) page.
We compare this list to the `buttondata.json` list to determine which buttons are unimplemented on prod site.

## [buttonstats.json](public/buttonstats.json)

This info does not require authentication. Its public data at a dev site.
[button_stats.html](http://stats.dev.buttonweavers.com/ui/stats/button_stats.html)
The python file [statslurp.py](statslurp.py) can parse that html to create `buttonstats.json`.
dont forget to install the python requirements

```shell
python3 -m pip install -r requirements.txt
python3 statslurp.py
```
// TODO consider if using this JSON is possible http://stats.dev.buttonweavers.com/ui/stats/win_percentage_stats.json


