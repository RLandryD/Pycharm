/*
* The integration developer needs to create the method processData 
 This method takes Message object of package com.sap.gateway.ip.core.customdev.util
 which includes helper methods useful for the content developer:
 The methods available are:
    public java.lang.Object getBody()
	public void setBody(java.lang.Object exchangeBody)
   public java.util.Map<java.lang.String,java.lang.Object> getHeaders()
    public void setHeaders(java.util.Map<java.lang.String,java.lang.Object> exchangeHeaders)
    public void setHeader(java.lang.String name, java.lang.Object value)
    public java.util.Map<java.lang.String,java.lang.Object> getProperties()
    public void setProperties(java.util.Map<java.lang.String,java.lang.Object> exchangeProperties) 
 */
importClass(com.sap.gateway.ip.core.customdev.util.Message);
importClass(java.util.HashMap);

function processData(message) {
    const oauthRequest = {
      url: casUrl + "client/" + realm + "?client_id=" + clientId + "&client_secret=" + clientSecret,
      method: "POST",
      header: "Content-Type: application/json"
    };
    
    pm.sendRequest(oauthRequest, function(error, response) {
        if (error === null) {
            var responseJson = response.json();
            var apiKey = responseJson.access_token;
            console.log("Generated new token");
        }
    });
     
     message.setHeader("X-Api-Key", apiKey);
     return message;
}

const realm = "wfs_eng_valid";
const clientId = "serviceaccount";
const clientSecret = "bfde7333-7a40-433a-bef2-87503ee2d9a0";
const casUrl = "https://cas-sprint-us4.dev.wfsaas.com/aug/api/v1.0/authenticate/";
