importClass(com.sap.gateway.ip.core.customdev.util.Message);
importClass(java.util.HashMap);
importClass(java.text.SimpleDateFormat);
importClass(java.util.Calendar);
importClass(java.util.TimeZone);
function processData(message) {
    var body = message.getBody( new java.lang.String().getClass());
    var headers = message.getHeaders();
    var clientid = headers.get("clientid").toString();
    var properties = message.getProperties();
    var on24Url = properties.get("on24Url");
    var lastrun = headers.get("lastrundatetime");
    var pagenumber = headers.get("pagenumber"); // Retrive all the incoming headers to construct the ON24 url
    var delta = headers.get("delta");
  //convert the lastrun to PST as ON24's base timezone is PST
  
    var inFormat = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss");
    var outFormat = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss");
    outFormat.setTimeZone(TimeZone.getTimeZone("PST"));
    
    var startDate,lastrunObj;
    var dateAsObject;
    try {
        lastrunObj = outFormat.format(inFormat.parse(lastrun.toString()));
        startDate = outFormat.parse(lastrunObj.toString());
    } catch (e) {
        var currDate = new Date().toJSON().slice(0,19);
        startDate = outFormat.parse(currDate.toString());
        lastrunObj = outFormat.format(inFormat.parse(currDate.toString()));
    }
    // Construct the ON24 url for the HTTPS call to get webinar events.
   var deltaCondition = "";
    if(delta == "true") {
        deltaCondition = "&includeInactive=Y&dateFilterMode=updated&filterOrder=desc";
    }
    var url = on24Url + "/client/"+clientid+"/event?includesubaccounts=Y&contentType=webcast&startDate="+lastrunObj+"&endDate="+calculateEndDate(startDate)+"&pageoffset="+pagenumber+deltaCondition;
    
    message.setProperty("on24Eventsurl", url);
    message.setProperty("timezones", body);
    return message;
}

function calculateEndDate(startDate) {
    // Format the dates to PST timezone and also calculate endDate as 6 months from startDate
    var formatter = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss"); 
    formatter.setTimeZone(TimeZone.getTimeZone("PST"));
    var cal = Calendar.getInstance();
    cal.setTime(startDate);
    cal.add(Calendar.DAY_OF_MONTH, 180);
    var dateAsObjAfter6Months = cal.getTime();
    return formatter.format(dateAsObjAfter6Months);
}