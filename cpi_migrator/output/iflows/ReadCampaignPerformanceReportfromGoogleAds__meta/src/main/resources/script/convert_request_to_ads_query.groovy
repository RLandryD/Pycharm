/* Map Google Adwords request to Ads query for campaign performance report
 */
import com.sap.gateway.ip.core.customdev.util.Message;
import java.util.HashMap;
import groovy.json.*;
import groovy.xml.*;

def Message processData(Message message) {


    def body = message.getBody(java.lang.String); 
    def fields="";
    def conditions="";
    def campaignIds="";
    def seperator;
    
    //set property: login customer id -- for OAuth purpose
    def customerId = message.getProperty("ROOT_MCC_ACCOUNT").replaceAll("-","");
    message.setProperty("LoginCustomerId", customerId.trim());
    
    //Parse the xml
    def root = new XmlSlurper().parseText(body);
    
    //set property TargetClientCustomerId - query resource path
    if(root.reportHeader.clientCustomerId!=null){
        def clientCustomerId = root.reportHeader.clientCustomerId.toString();
        message.setProperty("TargetClientCustomerId", clientCustomerId.trim());
    }else{
         throw new IllegalStateException("No clientCustomerId is provided");
    }


    //Set Ads fields for the query selection
    root.selector.fields.each{ value ->
    
        if(fields == ""){
            seperator = "";
        }else{
            seperator = ",";
        };
        
        switch (value.text()){
            case "CampaignId":
                fields = fields + seperator + "campaign.id";
                break;
            case "CampaignName":
                fields = fields + seperator + "campaign.name";
                break;            
            case "AccountTimeZone":
                fields = fields + seperator + "customer.time_zone" ;
                break;
            case "AccountCurrencyCode":
                fields = fields + seperator + "customer.currency_code" ;
                break;
            case "Date":
                fields = fields + seperator + "segments.date";
                break;
            case "Device":
                fields = fields + seperator + "segments.device";
                break;
            case "AdNetworkType2":
                fields = fields + seperator + "segments.ad_network_type";
                break;
            case "Cost":
                fields = fields + seperator + "metrics.cost_micros";
                break;
            case "Impressions":
                fields = fields + seperator + "metrics.impressions";
                break;            
            case "Clicks":
                fields = fields + seperator + "metrics.clicks";
                break;
            case "VideoViews":
                fields = fields + seperator + "metrics.video_views";
                break;
            case "VideoViewRate":
                fields = fields + seperator + "metrics.video_view_rate";
                break;
            case "InvalidClicks":
                fields = fields + seperator + "metrics.invalid_clicks";
                break;
        }
        
    }


    // Add date range for query conditions
    if(root.dateRangeType == "CUSTOM_DATE") {
        if( root.selector.dateRange != null && root.selector.dateRange.min != null && root.selector.dateRange.max != null){
            def minDate = formateDate(root.selector.dateRange.min.text());
            def maxDate = formateDate(root.selector.dateRange.max.text());
            conditions = "segments.date BETWEEN '" + minDate + "' AND '" + maxDate + "'"; 
        };
    }else{ // fixed date range
        conditions = "segments.date DURING " + root.dateRangeType; 
    }

    // Parse predicates: campaign IDs, page token(optional)
    def pageToken = "";
    root.selector.predicates.each{ p ->
        switch(p.field.text()){
            case "CampaignId":
                p.values.each{ v ->
                    if(campaignIds == ""){
                        campaignIds = v.text();
                    }else{
                        campaignIds = campaignIds + "," + v.text();
                    }
                }
                break;
            case "pageToken":
                pageToken = p.values[0].text();
                break;
        }
    }
    
    // Add campaign id to conditions
    if (campaignIds!=""){
        conditions =  "campaign.id IN (" + campaignIds + ") AND " + conditions;
    }
    
    if( fields == ""  || conditions == "" ){
        // raise exception for missing selected fields or where clause is empty
    }
    
    //Buid request Body
    def queryString = "SELECT " + fields + " FROM campaign WHERE " + conditions;
    def jsonRequest = new JsonBuilder();

    if(pageToken != ""){ // add page token if is provided in the request
        jsonRequest query: queryString, pageToken: pageToken
    }else{
	    jsonRequest query: queryString
    }
    

	message.setBody(jsonRequest.toString()); 
    return message;


}

// The date format from yyyymmdd to yyyy-mm-dd
def String formateDate(String date){
    String year = date.substring(0,4);
    String month = date.substring(4,6);
    String day = date.substring(6);
    date = year + "-" + month + "-" + day;
    return date;
}