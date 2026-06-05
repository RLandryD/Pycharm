importClass(com.sap.gateway.ip.core.customdev.util.Message);
importClass(java.util.HashMap);

function processData(message) {
	
	var map = message.getProperties();
    var addressDataJson = map.get("addressDataJson");
    if(!addressDataJson){
    	return message;
    }
    addressDataJson = JSON.parse(addressDataJson);
    
	var iDoc = JSON.parse(message.getBody().toString());
	addressDataJson.mixins.iDoc_ADRMAS.E1ADRMAS = {};
    copy(addressDataJson.mixins.iDoc_ADRMAS.E1ADRMAS, iDoc.ADRMAS03.IDOC.E1ADRMAS);
	
	//message.setProperty("iDocMapped", iDocMapped);

	message.setBody(JSON.stringify(addressDataJson));
	return message;
}

function copy(json1, json2){
	
	var arrayType = {"E1BPAD1VL": true, "E1BPADTEL": true, "E1BPADFAX": true, "E1BPADTTX": true, "E1BPADTLX": true, "E1BPADSMTP": true, "E1BPADRML": true, "E1BPADX400": true, "E1BPADRFC": true, "E1BPADPRT": true, "E1BPADSSF": true, "E1BPADURI": true, "E1BPADPAG": true, "E1BPAD_REM": true, "E1BPCOMREM": true, "E1BPADUSE": true}; 
	var json = json1;
	for(var i in json2){
		if(i === "@SEGMENT"){
			
		}
		else if(typeof json2[i] === "string"){
			json1[i.toLowerCase()] = json2[i];
		}
		else if(typeof json2[i] === "object"){
			if (arrayType[i]){ //handle arrays
				if(json2[i].length){
					json1[i] = copy([], json2[i]); //copy array into array
				}
				else{
					json1[i] = [copy({}, json2[i])]; //copy json into array
				}
			}
			else{
				json1[i] = copy({}, json2[i]); //copy json into json
			}
		}
	}
	return json;
}